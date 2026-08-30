"""Runtime entrypoint for the Jibot VDA5050 adapter.

Initializes robot, IO, motor, MQTT connection state, and runs adapter tasks.
"""

import asyncio
import argparse
import datetime
import json
import sys
import random
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

ADAPTER_ROOT = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[1]
for jibot_root in (ADAPTER_ROOT / "jibot", REPO_ROOT / "jibot"):
    if str(jibot_root) not in sys.path:
        sys.path.insert(0, str(jibot_root))
CLIENT_SRC = REPO_ROOT / "jibot-client" / "src"
if str(CLIENT_SRC) not in sys.path:
    sys.path.insert(0, str(CLIENT_SRC))
SIMULATOR_ROOT = REPO_ROOT / "jibot-simulator"
if str(SIMULATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(SIMULATOR_ROOT))
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from protocol.vda5050_3_0.messages import ConnectionState


from config.config import get_config, get_config_with_fallback
from jibot_client import JIBOT
from jibot_params import DEFAULT_BACKUP_ROOT, DEFAULT_SOURCE_DIR, DEFAULT_TARGET_DIR, sync_jibot_params
from jibot_position_store import load_position_snapshot, save_position_snapshot
from jibot_map_store import load_map_snapshot, save_map_snapshot
from utils.helpers import install_timestamped_logging

POSITION_STORE_PATH = ADAPTER_ROOT / "runtime" / "jibot-position.json"
MAP_STORE_PATH = ADAPTER_ROOT / "runtime" / "jibot-map.json"


def resolve_initial_position(snapshot, *, x=None, y=None, theta=None):
    """Resolve the simulator's start pose from a saved snapshot + CLI overrides.

    Base pose comes from the saved position snapshot (or the origin when there
    is none). Each of --x/--y/--theta then overrides its axis, so partial flags
    work and the saved-position fallback stays usable when no flags are given.
    Returns None only when there is no snapshot and no CLI override at all.
    """
    have_override = x is not None or y is not None or theta is not None
    if snapshot is None and not have_override:
        return None
    base = dict(snapshot) if snapshot else {"x": 0.0, "y": 0.0, "theta": 0.0}
    if x is not None:
        base["x"] = float(x)
    if y is not None:
        base["y"] = float(y)
    if theta is not None:
        base["theta"] = float(theta)
    return base


def resolve_simulator_initial_position(snapshot, initial_map, *, x=None, y=None, theta=None):
    """Resolve explicit simulator pose, leaving loaded-map placement random.

    Operator CLI pose flags are explicit and keep the old override behavior.
    Without CLI pose flags, a loaded map lets ``SimulatedJIBOT`` choose a random
    node so simulator state starts with a meaningful random lastNodeId.
    """
    have_override = x is not None or y is not None or theta is not None
    if have_override:
        return resolve_initial_position(snapshot, x=x, y=y, theta=theta)
    if initial_map is not None and initial_map.get("nodes"):
        return None
    return resolve_initial_position(snapshot)


def parse_mqtt_address(value: str) -> Tuple[str, int]:
    host, separator, port_text = value.rpartition(":")
    if not separator or not host or not port_text:
        raise argparse.ArgumentTypeError("MQTT address must be in host:port format.")

    try:
        port = int(port_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("MQTT address port must be an integer.") from exc

    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError("MQTT address port must be between 1 and 65535.")

    return host, port


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the JIBOT VDA5050 adapter.")
    parser.add_argument(
        "--simulator",
        action="store_true",
        help="Use the local JIBOT simulator instead of connecting to the real AMR.",
    )
    parser.add_argument(
        "--config",
        help="Path to a per-instance config TOML. Default: config/config.toml. "
        "Use this to give each adaptor its own settings when running several.",
    )
    parser.add_argument(
        "--robots",
        help="Path to the fleet file for --robot. Default: config/robots.hcl.",
    )
    parser.add_argument(
        "--robot",
        help="Start the robot with this id from the fleet file (config/robots.hcl). "
        "Its id/ip/ezi settings are applied as config overrides. Explicit flags "
        "below still win. This is what the per-robot systemd unit "
        "(amr-adaptor@<id>.service) uses.",
    )
    parser.add_argument(
        "--id",
        "--serial-number",
        dest="serial_number",
        help="Override the robot id from robots.hcl. This is the VDA5050 "
        "identity and drives the MQTT topic prefix, so each adaptor instance "
        "must use a unique value.",
    )
    parser.add_argument(
        "--vehicle-ip",
        help="Override the robot control IP for this process.",
    )
    parser.add_argument(
        "--vehicle-port",
        type=int,
        help="Override vehicle.vehicle_port from config/config.toml.",
    )
    parser.add_argument(
        "--ezi-io",
        help="Override the EZI IO module IP for this process.",
    )
    parser.add_argument(
        "--ezi-motor",
        help="Override the EZI motor driver IP for this process.",
    )
    parser.add_argument(
        "--mqtt-address",
        type=parse_mqtt_address,
        metavar="HOST:PORT",
        help="Override mqtt_broker host and port at once from config.",
    )
    parser.add_argument(
        "--mqtt-host",
        help="Override mqtt_broker.host from config.",
    )
    parser.add_argument(
        "--mqtt-port",
        type=int,
        help="Override mqtt_broker.port from config.",
    )
    parser.add_argument(
        "--x",
        type=float,
        help="Simulator only: set the start pose X at runtime. Overrides the "
        "saved position snapshot for this axis. Use with --y/--theta.",
    )
    parser.add_argument(
        "--y",
        type=float,
        help="Simulator only: set the start pose Y at runtime. Overrides the "
        "saved position snapshot for this axis.",
    )
    parser.add_argument(
        "--theta",
        type=float,
        help="Simulator only: set the start pose theta (rad) at runtime. "
        "Overrides the saved position snapshot for this axis.",
    )
    parser.add_argument(
        "--battery",
        type=float,
        help="Simulator only: set the start battery percentage (0-100) at "
        "runtime. Clamped to range; without this the simulator starts at a "
        "random charge.",
    )
    parser.add_argument(
        "--charging",
        action="store_true",
        help="Simulator only: boot up docked & charging (status=charging, "
        "battery climbing) as if the robot was left on its charger.",
    )
    parser.add_argument(
        "--vehicle-smoke-test",
        action="store_true",
        help="Connect to the vehicle, send basic status commands, print state, then exit before MQTT/EZI setup.",
    )
    parser.add_argument(
        "--vehicle-console",
        action="store_true",
        help="Open an interactive terminal console for sending JIBOT commands before MQTT/EZI setup.",
    )
    parser.add_argument(
        "--sync-jibot-params",
        action="store_true",
        help="Copy JIBOT map/routes params into the adapter after backing up existing files, then exit.",
    )
    parser.add_argument(
        "--jibot-params-source",
        default=str(DEFAULT_SOURCE_DIR),
        help=f"Source params directory for --sync-jibot-params. Default: {DEFAULT_SOURCE_DIR}",
    )
    parser.add_argument(
        "--jibot-params-dest",
        default=str(DEFAULT_TARGET_DIR),
        help=f"Destination params directory for --sync-jibot-params. Default: {DEFAULT_TARGET_DIR}",
    )
    parser.add_argument(
        "--jibot-params-backup-dir",
        default=str(DEFAULT_BACKUP_ROOT),
        help=f"Backup root for --sync-jibot-params. Default: {DEFAULT_BACKUP_ROOT}",
    )
    return parser.parse_args(argv)


def _cli_overrides(cli_args) -> Dict[str, Dict[str, Any]]:
    """Explicit CLI flags -> nested config-override dict. None values dropped."""
    overrides: Dict[str, Dict[str, Any]] = {"vehicle": {}, "ezi": {}, "mqtt_broker": {}}
    if cli_args.serial_number:
        overrides["vehicle"]["serial_number"] = cli_args.serial_number
    if cli_args.vehicle_ip:
        overrides["vehicle"]["vehicle_ip"] = cli_args.vehicle_ip
    if cli_args.vehicle_port:
        overrides["vehicle"]["vehicle_port"] = cli_args.vehicle_port
    if cli_args.ezi_io:
        overrides["ezi"]["ezi_io"] = cli_args.ezi_io
    if cli_args.ezi_motor:
        overrides["ezi"]["ezi_motor"] = cli_args.ezi_motor
    if cli_args.mqtt_address is not None:
        overrides["mqtt_broker"]["host"], overrides["mqtt_broker"]["port"] = cli_args.mqtt_address
    if cli_args.mqtt_host:
        overrides["mqtt_broker"]["host"] = cli_args.mqtt_host
    if cli_args.mqtt_port:
        overrides["mqtt_broker"]["port"] = cli_args.mqtt_port
    return {section: vals for section, vals in overrides.items() if vals}


def apply_cli_overrides(config, cli_args) -> None:
    """Apply explicit CLI overrides to an already-loaded config object."""
    for section, values in _cli_overrides(cli_args).items():
        target = getattr(config, section)
        for key, value in values.items():
            setattr(target, key, value)


def resolve_instance(cli_args):
    """Resolve (overrides, config_path, simulator, extensions_path, recipes_path).

    이 인스턴스가 쓸 config override / 전용 TOML 경로 / 시뮬레이터 여부 /
    extensions.hcl 경로를 정한다. ``--robot``이 주어지면 robots.hcl의 해당
    로봇 값을 쓴다. ``--robot``이 없고 명시적 CLI override도 없으면 robots.hcl에
    단일 항목만 허용해 그 로봇을 자동 선택한다. 명시적 CLI 플래그(--id/--vehicle-ip
    ...)는 디버깅/콘솔 실행을 위해 항상 우선한다.
    """
    overrides: Dict[str, Dict[str, Any]] = {}
    config_path = cli_args.config
    simulator = cli_args.simulator
    extensions_path = None
    recipes_path = None

    if cli_args.robot:
        from config.fleet import (
            find_robot,
            load_fleet,
            resolve_robot_path,
            robot_overrides,
        )

        # 꺼진 블록까지 읽는다. find_robot이 "없다" 대신 "꺼져 있다"라고
        # 말해 줘야 운영자가 파일에 보이는 블록을 두고 오타를 찾지 않는다.
        robot = find_robot(
            load_fleet(cli_args.robots, include_disabled=True), cli_args.robot
        )
        overrides = robot_overrides(robot)
        if config_path is None:
            resolved = resolve_robot_path(robot, "config", cli_args.robots)
            if resolved is not None:
                config_path = str(resolved)
        resolved_ext = resolve_robot_path(robot, "extensions", cli_args.robots)
        if resolved_ext is not None:
            extensions_path = str(resolved_ext)
        resolved_recipes = resolve_robot_path(robot, "recipes", cli_args.robots)
        if resolved_recipes is not None:
            recipes_path = str(resolved_recipes)
        if robot.get("simulator"):
            simulator = True
    elif not _cli_overrides(cli_args):
        from config.fleet import load_fleet, resolve_robot_path, robot_overrides

        robots = load_fleet(cli_args.robots)
        if len(robots) != 1:
            raise SystemExit(
                "robots.hcl has multiple robot blocks; use run_multi.py "
                "or pass --robot <id> for a single main.py process"
            )
        robot = robots[0]
        overrides = robot_overrides(robot)
        if config_path is None:
            resolved = resolve_robot_path(robot, "config", cli_args.robots)
            if resolved is not None:
                config_path = str(resolved)
        resolved_ext = resolve_robot_path(robot, "extensions", cli_args.robots)
        if resolved_ext is not None:
            extensions_path = str(resolved_ext)
        resolved_recipes = resolve_robot_path(robot, "recipes", cli_args.robots)
        if resolved_recipes is not None:
            recipes_path = str(resolved_recipes)
        if robot.get("simulator"):
            simulator = True
    # Explicit CLI flags win over the fleet defaults.
    cli = _cli_overrides(cli_args)
    for section, values in cli.items():
        overrides.setdefault(section, {}).update(values)

    return overrides, config_path, simulator, extensions_path, recipes_path


# get robot data
async def robot_info_loop(
    vehicle,
    interval_sec=1,
    position_store_path: Optional[Path] = None,
    position_map_id: str = "",
    position_serial_number: str = "",
    map_store_path: Optional[Path] = None,
    task_info_poll_sec: float = 5.0,
):
    map_fetch_attempted = False
    # Cur-task/task-info/path are diagnostic-only, so they poll on their own
    # slower cadence. 0.0 forces the first cycle to send them immediately.
    last_task_info_poll = 0.0
    while True:
        # Skip polling while the JIBOT link is down. Writing to a dead socket
        # would raise and kill this loop; the adapter's reconnect supervisor
        # restores the connection and polling resumes automatically.
        is_connected = getattr(vehicle, "is_connected", None)
        if callable(is_connected) and not is_connected():
            await asyncio.sleep(interval_sec)
            continue

        try:
            await vehicle.get_robot_info(interval_sec * 1000)
            await vehicle.get_motor_state(interval_sec * 1000)
            await vehicle.get_localization_info(interval_sec * 1000)
            # Diagnostic-only polls (WebUI status + arrival mismatch refs); run
            # them on the slower task_info cadence instead of every cycle so they
            # don't flood the JIBOT link/logs. <=0 falls back to every cycle.
            now = time.monotonic()
            if task_info_poll_sec <= 0 or now - last_task_info_poll >= task_info_poll_sec:
                last_task_info_poll = now
                for method_name in ("um_get_cur_task", "um_get_task_info", "um_get_path"):
                    method = getattr(vehicle, method_name, None)
                    if callable(method):
                        await method(gap=-1)
            # Load node positions once on startup. Later getMap requests are
            # handled explicitly through the VDA5050 instant-action path.
            is_simulator = bool(getattr(vehicle, "is_simulator", False))
            get_map = getattr(vehicle, "get_map", None)
            if callable(get_map) and not is_simulator and not map_fetch_attempted:
                map_fetch_attempted = True
                if not getattr(vehicle, "_map_nodes", None):
                    nodes = await get_map()
                    snapshot_nodes = dict(nodes or {})
                    # PathPoint owns the FMS driving topology. Persist it with
                    # semantic Goal/Dock nodes so simulator/replay starts from
                    # the same node graph as the real robot.
                    snapshot_nodes.update(
                        getattr(vehicle, "_map_path_points", None) or {}
                    )
                    # Persist the real robot's map so a later --simulator run
                    # can rebuild itself from the real station layout. Only save
                    # while the link is actually up: get_map() returns the cached
                    # nodes on a timeout, so without this check a flaky link could
                    # re-save stale data. _map_raw confirms this poll really
                    # parsed a fresh UmGetMap response.
                    still_connected = not callable(is_connected) or is_connected()
                    if (
                        map_store_path is not None
                        and snapshot_nodes
                        and still_connected
                        and getattr(vehicle, "_map_raw", None) is not None
                    ):
                        saved = save_map_snapshot(
                            map_store_path,
                            map_id=position_map_id,
                            nodes=snapshot_nodes,
                            raw=vehicle._map_raw,
                            serial_number=position_serial_number,
                        )
                        if saved:
                            print(
                                f"[JIBOT MAP SAVED] {len(snapshot_nodes)} nodes saved to "
                                f"{Path(map_store_path).resolve()}"
                            )
            if position_store_path is not None and not is_simulator:
                save_position_snapshot(
                    position_store_path,
                    x=getattr(vehicle, "_x", None),
                    y=getattr(vehicle, "_y", None),
                    theta=getattr(vehicle, "_th", None),
                    map_id=position_map_id,
                    serial_number=position_serial_number,
                )
        except (BrokenPipeError, ConnectionError, OSError, asyncio.TimeoutError) as exc:
            # Never let a transient poll error kill the supervisor loop: a dropped
            # socket or an unanswered request (TimeoutError) just skips this cycle;
            # the reconnect supervisor restores the link and polling resumes.
            print(f"[JIBOT INFO LOOP] poll skipped: {exc!r}")

        await asyncio.sleep(interval_sec)


async def input_async(prompt: str) -> str:
    if not sys.stdin.isatty():
        print(prompt, end="", flush=True)
        line = sys.stdin.readline()
        if line == "":
            raise EOFError("stdin closed")
        return line.rstrip("\n")

    return await asyncio.to_thread(input, prompt)


def parse_console_value(raw: str) -> Any:
    value = raw.strip()
    if value == "":
        return None

    lowered = value.lower()
    if lowered in ("none", "null"):
        return None
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def print_vehicle_snapshot(vehicle) -> None:
    print(
        "Vehicle state:",
        {
            "mode": vehicle._mode,
            "status": vehicle._status,
            "battery": vehicle._battery,
            "motor_flag": vehicle._motor_flag,
            "localization_score": vehicle._localization_score,
            "station": vehicle._station,
            "charging": vehicle._charging,
            "x": vehicle._x,
            "y": vehicle._y,
            "th": vehicle._th,
        },
    )


async def run_vehicle_console(vehicle: JIBOT) -> None:
    print(f"Connected to JIBOT vehicle at {vehicle.robot_ip}:{vehicle.robot_port}")
    print_vehicle_snapshot(vehicle)
    print("Type a command number/name, 'list', 'state', or 'quit'.")

    commands = sorted(vehicle.get_available_commands())

    while True:
        selection = (await input_async("jibot> ")).strip()
        if not selection:
            continue

        lowered = selection.lower()
        if lowered in ("q", "quit", "exit"):
            return
        if lowered in ("l", "list", "?"):
            for index, command in enumerate(commands, start=1):
                spec = vehicle.get_command_spec(command)
                params = ", ".join(spec["params"]) or "-"
                print(f"{index:2}. {command:<28} params: {params}")
            continue
        if lowered in ("s", "state"):
            print_vehicle_snapshot(vehicle)
            continue

        command = None
        if selection.isdigit():
            index = int(selection)
            if 1 <= index <= len(commands):
                command = commands[index - 1]
        elif selection in vehicle.COMMAND_SPECS:
            command = selection

        if command is None:
            print("Unknown command. Type 'list' to see available commands.")
            continue

        spec = vehicle.get_command_spec(command)
        print(f"Selected {command}")
        gap_raw = await input_async("  #GAP# [-1]: ")
        gap = parse_console_value(gap_raw)
        if gap is None:
            gap = -1

        params = {}
        for param_name in spec["params"]:
            raw_value = await input_async(f"  {param_name}: ")
            params[param_name] = parse_console_value(raw_value)

        timeout_raw = await input_async("  response timeout sec [3]: ")
        timeout = parse_console_value(timeout_raw)
        if timeout is None:
            timeout = 3.0

        try:
            if command == "UmConnect":
                await vehicle.um_connect(gap=gap)
                response = await vehicle.wait_for_response(command=command, timeout=float(timeout))
            else:
                response = await vehicle.send_command_and_wait(
                    command,
                    gap=gap,
                    timeout=float(timeout),
                    **params,
                )
        except Exception as exc:
            print(f"Command failed: {exc}")
            continue

        if response is None:
            print("No matching response before timeout.")
        else:
            print(json.dumps(response, ensure_ascii=False, indent=2))
        print_vehicle_snapshot(vehicle)


async def try_connect_vehicle(vehicle) -> bool:
    """Attempt the initial JIBOT connection without crashing on failure.

    초기 JIBOT 연결을 시도하되 실패해도 종료하지 않는다.

    A failed first connection is non-fatal: the adapter still starts, publishes
    a FATAL JIBOT_CONNECTION_LOST state, and its reconnect supervisor keeps
    retrying every configured interval until the robot comes online.
    첫 연결 실패는 치명적이지 않다. adapter는 그대로 시작되어 FATAL
    JIBOT_CONNECTION_LOST 상태를 발행하고, 재연결 감시 루프가 설정된 간격마다
    로봇이 켜질 때까지 재시도한다.
    """
    # The simulator is an in-process stand-in: it has no real TCP socket or
    # JIBOT API to log in to, and its connect_socket()/connect() overrides print
    # their own "[JIBOT SIM] ..." lines. Skip the real-JIBOT handshake logging so
    # simulator runs don't look like they are talking to a physical robot. The
    # calls themselves stay — they set is_connected()=True, which the link-health
    # check needs to avoid a spurious FATAL JIBOT_CONNECTION_LOST state.
    is_simulator = bool(getattr(vehicle, "is_simulator", False))
    try:
        await vehicle.connect_socket()
        if not is_simulator:
            print("[JIBOT API CONNECTING] command=UmConnect")
        await vehicle.connect()
        if not is_simulator:
            print("[JIBOT API CONNECTED] command=UmConnect sent")
        return True
    except (BrokenPipeError, ConnectionError, OSError) as exc:
        print(f"[JIBOT CONNECT FAILED] {exc}; adapter will keep retrying.")
        return False


async def disconnect_vehicle(vehicle):
    try:
        await vehicle.disconnect()
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
        print(f"[JIBOT TCP DISCONNECT] socket already closed: {exc}")


def config_error_path(error, *, config_path, extensions_path, recipes_path):
    """FATAL CONFIG_LOAD_FAILED가 가리킬 "고쳐야 할 파일".

    ConfigErrorReporter가 이 값을 errorReferences.configPath로 싣는다. 깨진 것이
    extensions.hcl인데 config.toml을 가리키면 운영자는 멀쩡한 파일을 뒤지게 된다.

    경로가 비어 있으면(robots.hcl에 per-robot 경로가 없는 흔한 경우) 로더가 실제로
    읽는 기본 파일 이름을 대신 싣는다 — None을 그대로 보내면 단서가 아예 사라진다.
    """
    from config.config import DEFAULT_CONFIG_PATH
    from config.errors import ConfigError
    from config.extensions import DEFAULT_EXTENSIONS_PATH, ExtensionsError
    from config.recipes import DEFAULT_RECIPES_PATH, RecipesError

    # 섹션 검증(_section)이 만든 오류는 어느 파일에서 온 키인지 이미 알고 있다.
    # 타입만 보고 되짚으면 robots.hcl에서 온 키를 extensions.hcl이라고 말하게 된다.
    if isinstance(error, ConfigError) and error.path:
        return error.path
    if isinstance(error, ExtensionsError):
        return str(extensions_path or DEFAULT_EXTENSIONS_PATH)
    if isinstance(error, RecipesError):
        return str(recipes_path or DEFAULT_RECIPES_PATH)
    # 라벨 없는 KeyError/TypeError도 config-error 모드로 오게 됐다
    # (get_config_with_fallback이 예외 타입을 가리지 않는다). --robot 한 대만 있는
    # 흔한 경우 config_path는 None이고, 그대로 실으면 configPath 자체가 빠져
    # 나간다 — 로더가 실제로 읽는 파일 이름이라도 남긴다.
    return str(config_path or DEFAULT_CONFIG_PATH)


async def run_config_error_mode(config_data, config_path, error):
    """Stay alive in a degraded "config error" mode instead of dying silently.

    The per-instance config TOML could not be loaded, so there is no vehicle to
    drive. Using the fallback config (base config.toml + this instance's
    overrides) we still reach the broker and publish a FATAL CONFIG_LOAD_FAILED
    state on a heartbeat, so the misconfiguration is visible on the FMS/WebUI and
    an operator can fix the missing/broken config and restart the adapter.
    """
    from core.config_error_reporter import ConfigErrorReporter

    print(f"Error: failed to load config {config_path!r}: {error}")
    print(
        "[CONFIG ERROR MODE] entering degraded config-error mode; publishing a "
        "FATAL CONFIG_LOAD_FAILED state to MQTT so operators can see and respond. "
        "Vehicle/motor/IO are not started."
    )
    reporter = ConfigErrorReporter(
        config=config_data, config_path=config_path, error=error
    )
    await reporter.run(heartbeat_sec=5.0)


async def attach_ezi_clients(adapter, ezi_config, *, simulator=False):
    """설정된 주소가 있는 EZI 모듈만 Adapter에 붙인다.

    ezi_io/ezi_motor는 로봇별 주소라 config/robots.hcl이 채운다. 시뮬레이터나
    하드웨어가 없는 개발 장비에서는 비어 있는 게 정상이고, 빈 주소로 소켓을 열면
    getaddrinfo가 gaierror("Name or service not known")를 던져 main()의 넓은
    except가 이를 받아 adaptor 전체가 내려간다. 미설정 모듈은 붙이지 않고 넘어가며,
    _ezi_io/_ezi_motor가 None인 상태는 extensions(pio/ezio/clamp/facility)와
    io 스냅샷이 이미 "미설정"으로 처리한다.

    ``simulator``이고 주소가 비어 있으면 그 대신 가짜 IO 보드를 붙인다
    (utils/io_simulator). 그래야 하드웨어 없이도 pio*/ezio* recipe의 순서·cleanup·
    timeout을 끝까지 볼 수 있다. 주소가 설정돼 있으면 --simulator라도 실장비가
    이긴다 — 시험 보드를 붙여 놓고 실행하는 경우가 그쪽이다.

    설정된 주소인데 부착에 실패하면 그 모듈만 포기한다. adaptor는 MQTT로 상태를
    계속 보고해야 하므로 하드웨어 하나 때문에 프로세스가 죽어서는 안 된다.

    Returns: 부착된 EziMotorClient, 없으면 None (모터 테스트 블록이 쓴다).
    """
    from utils.ezi_io import EZIIOClient
    from utils.ezi_motor import EziMotorClient

    if ezi_config.ezi_io:
        try:
            io = EZIIOClient(ezi_config.ezi_io)
            await io.connect()
            info_io = await io.get_board_info()
            if info_io:
                print("Ready :", info_io["description"])
            adapter.set_ezi_io(io)
        except Exception as exc:
            print(
                f"[EZI IO] attach failed at {ezi_config.ezi_io} "
                f"({exc}); continuing without EZI IO"
            )
    elif simulator:
        # 직렬 PIO master도 함께 붙인다. pioInit의 pairing은 EZI IO의 SELECT/GO와
        # 직렬 BC가 맞물려야 성립하므로 한쪽만 가짜면 아무 recipe도 못 지나간다.
        from utils.io_simulator import make_simulated_io

        sim_io, sim_pio = make_simulated_io(ezi_config)
        await sim_io.connect()
        info_io = await sim_io.get_board_info()
        adapter.set_ezi_io(sim_io)
        adapter.set_pio_client_factory(lambda: sim_pio)
        print(
            f"[EZI IO] no address configured (ezi.ezi_io); "
            f"attached {info_io['description']} + simulated PIO master"
        )
    else:
        print("[EZI IO] no address configured (ezi.ezi_io); skipping")

    motor = None
    if ezi_config.ezi_motor:
        try:
            motor = EziMotorClient(
                ezi_config.ezi_motor,
                poll_interval_sec=ezi_config.ezi_motor_poll_interval_sec,
            )
            info_motor = await motor.get_board_info()
            if info_motor:
                print("Ready :", info_motor["description"])
            adapter.set_ezi_motor(motor)
        except Exception as exc:
            motor = None
            print(
                f"[EZI MOTOR] attach failed at {ezi_config.ezi_motor} "
                f"({exc}); continuing without EZI motor"
            )
    else:
        print("[EZI MOTOR] no address configured (ezi.ezi_motor); skipping")

    return motor


def release_charge_hold(adapter):
    """종료 시 제자리 충전 릴레이 유지를 해제한다(실패는 로그만 남기고 무시).

    해제 로직은 Adapter 메서드가 아니라 extensions.charge의 함수다. 종료 경로가
    옛 메서드 이름을 부르면 AttributeError가 여기서 삼켜져 릴레이가 계속 붙어 있게 된다.
    """
    from extensions.charge import release_charge_in_place

    try:
        release_charge_in_place(adapter)
    except Exception as exc:
        print(f"[SHUTDOWN] charge-in-place release failed (ignored): {exc}")


async def main():
    # Prefix every log line with a timestamp.
    install_timestamped_logging()

    vehicle = None
    vehicle_task = None
    adapter = None
    config_data = None
    cli_args = parse_args()



    try:
        if cli_args.sync_jibot_params:
            result = sync_jibot_params(
                source_dir=Path(cli_args.jibot_params_source),
                target_dir=Path(cli_args.jibot_params_dest),
                backup_root=Path(cli_args.jibot_params_backup_dir),
            )
            print(f"[JIBOT PARAMS] copied to {result.target_dir}")
            for copied_dir in result.copied:
                print(f"[JIBOT PARAMS] updated {copied_dir}")
            if result.backup_dir is None:
                print("[JIBOT PARAMS] no existing map/routes directories to back up")
            else:
                print(f"[JIBOT PARAMS] backup saved to {result.backup_dir}")
            return

        # Load configuration, applying any per-instance overrides. This is what
        # makes running several adaptors possible: each process gets its own
        # id/ip/module addresses while sharing one config.toml template. With
        # --robot the values come from config/robots.hcl (the fleet file).
        (
            overrides,
            config_path,
            use_simulator,
            extensions_path,
            recipes_path,
        ) = resolve_instance(cli_args)
        try:
            config_data, config_error = get_config_with_fallback(
                config_path=config_path,
                overrides=overrides,
                extensions_path=extensions_path,
                recipes_path=recipes_path,
            )
        except Exception as fallback_exc:
            # 보고용 config조차 못 만들었다 = 브로커/serialNumber를 못 찾았다는
            # 뜻이라 MQTT로 알릴 방법이 없다. non-zero로 빠져 systemd
            # (Restart=on-failure)가 재시작하게 둔다. 여기까지 오는 건 base
            # config.toml이나 출하 extensions/recipes까지 못 읽는 경우뿐이다 —
            # 설정 내용 오류는 아래 config-error 모드가 받는다.
            print(
                "Fatal: 설정을 읽지 못했고 보고용 config(base config.toml + "
                f"출하 extensions/recipes)도 만들지 못했습니다: {fallback_exc}"
            )
            raise SystemExit(1)

        if config_error is not None:
            # The per-instance config is missing/broken but the fallback gave us
            # a broker + serial. Report the failure over MQTT and stay alive in
            # config-error mode instead of dying silently. extensions.hcl /
            # recipes.hcl 실패도 여기로 온다 — 그 경우 보고할 파일은 config.toml이
            # 아니라 실제로 깨진 쪽이다.
            await run_config_error_mode(
                config_data,
                config_error_path(
                    config_error,
                    config_path=config_path,
                    extensions_path=extensions_path,
                    recipes_path=recipes_path,
                ),
                config_error,
            )
            return

        if not config_data.vehicle.serial_number:
            raise SystemExit(
                "vehicle serial_number is empty; set the robot block's label "
                "in config/robots.hcl or pass --id for an explicit debug run"
            )

        # Build Vehicle
        vehicle_ip = config_data.vehicle.vehicle_ip
        vehicle_port = config_data.vehicle.vehicle_port
        if cli_args.robot:
            vehicle_source = f"fleet:{cli_args.robot}"
        elif overrides.get("vehicle"):
            vehicle_source = "cli"
        else:
            vehicle_source = "config"
        print(
            f"[JIBOT CONFIG] id={config_data.vehicle.serial_number} "
            f"ip={vehicle_ip} port={vehicle_port} "
            f"source={vehicle_source} simulator={use_simulator}"
        )
        if use_simulator:
            from cls_jibot_simulator import SimulatedJIBOT

            print(f"Using simulated JIBOT AMR at {vehicle_ip}:{vehicle_port}.")
            snapshot = load_position_snapshot(POSITION_STORE_PATH)
            initial_map = load_map_snapshot(MAP_STORE_PATH)
            initial_position = resolve_simulator_initial_position(
                snapshot,
                initial_map,
                x=cli_args.x,
                y=cli_args.y,
                theta=cli_args.theta,
            )
            if initial_position is not None:
                source = "CLI/--x/--y/--theta" if (
                    cli_args.x is not None
                    or cli_args.y is not None
                    or cli_args.theta is not None
                ) else f"saved snapshot {POSITION_STORE_PATH}"
                print(
                    f"[JIBOT SIM] initial position from {source}: {initial_position}"
                )
            elif initial_map is not None and initial_map.get("nodes"):
                print("[JIBOT SIM] initial position from random saved map node")
            else:
                print(
                    "[JIBOT SIM] no start pose (no --x/--y/--theta, no saved "
                    f"snapshot at {POSITION_STORE_PATH}); starting at origin (0,0,0)"
                )
            if initial_map is not None:
                print(
                    "[JIBOT SIM] loaded saved map "
                    f"from {MAP_STORE_PATH}: {len(initial_map['nodes'])} nodes"
                )
            else:
                print(
                    f"[JIBOT SIM] no saved map at {MAP_STORE_PATH}; using FMS "
                    "order nodes only (run against the real robot once to save it)"
                )
            if cli_args.battery is not None:
                print(f"[JIBOT SIM] initial battery from --battery: {cli_args.battery}%")
            else:
                print("[JIBOT SIM] no --battery; starting at a random charge (30-100%)")
            if cli_args.charging:
                print("[JIBOT SIM] --charging: booting up docked & charging")
            vehicle = SimulatedJIBOT(
                vehicle_ip,
                vehicle_port,
                config=config_data,
                initial_position=initial_position,
                initial_map=initial_map,
                initial_battery=cli_args.battery,
                initial_charging=cli_args.charging,
            )
        else:
            jc = config_data.jibot_client
            vehicle = JIBOT(
                vehicle_ip,
                vehicle_port,
                config=config_data,
                charging_status=config_data.jibot_status.charging,
                user=jc.user,
                password=jc.password,
                device_type=jc.device_type,
                command_timeout=jc.command_timeout_sec,
                recv_buffer_bytes=jc.recv_buffer_bytes,
                status_log_interval_sec=jc.status_log_interval_sec,
                battery_log_interval_sec=jc.battery_log_interval_sec,
            )
        connected = await try_connect_vehicle(vehicle)

        if cli_args.vehicle_console:
            if not connected:
                print("Cannot open console: JIBOT vehicle is not connected.")
                return
            await run_vehicle_console(vehicle)
            return

        if cli_args.vehicle_smoke_test:
            if not connected:
                print("Cannot run smoke test: JIBOT vehicle is not connected.")
                return
            await vehicle.um_get_robot_info(gap=100)
            await vehicle.um_get_motor_state(gap=100)
            await vehicle.um_get_loc_state(gap=100)
            await asyncio.sleep(0.2)
            print(
                "Vehicle smoke test:",
                {
                    "mode": vehicle._mode,
                    "status": vehicle._status,
                    "battery": vehicle._battery,
                    "motor_flag": vehicle._motor_flag,
                    "localization_score": vehicle._localization_score,
                    "x": vehicle._x,
                    "y": vehicle._y,
                    "th": vehicle._th,
                },
            )
            return

        # Get vehicle data
        vehicle_task = asyncio.create_task(
            robot_info_loop(
                vehicle,
                1,
                position_store_path=POSITION_STORE_PATH,
                position_map_id=config_data.settings.map_id,
                position_serial_number=config_data.vehicle.serial_number,
                map_store_path=MAP_STORE_PATH,
                task_info_poll_sec=config_data.settings.task_info_poll_sec,
            )
        )

        # Stream BMS voltage/current from ROS /jrobot_status (UmGetBatteryInfo is
        # a 7273 stub). No-op under --simulator or when [bms_ros] is disabled.
        from bms_ros_listener import start_bms_listener
        bms_task = start_bms_listener(vehicle, config_data.bms_ros, vehicle.is_simulator)

        from adapter_jibot import Adapter

        # Build Adapter with this instance's config (id, broker, ezi modules).
        adapter = Adapter(
            config=config_data,
            config_path=config_path,
            extensions_path=extensions_path,
            recipes_path=recipes_path,
            robots_path=cli_args.robots,
        )

        # Attach JIBOT instance to Adapter
        adapter.set_vehicle(vehicle)
        if config_data.charge_circuit.enabled:
            from utils.charge_circuit import SubprocessChargeCircuit
            adapter.set_charge_circuit(SubprocessChargeCircuit(config_data.charge_circuit))
        adapter.configure_connection_last_will("connection", ConnectionState.OFFLINE)
        adapter.connect_mqtt()


        # Attach the EZI IO/motor modules this robot actually has.
        motor = await attach_ezi_clients(
            adapter, config_data.ezi_config, simulator=use_simulator
        )


        # Testing Motor OPEN/CLOSE
        if motor is not None and config_data.ezi_config.do_motor_test > 0:
            print("Do motor Test ", config_data.ezi_config.do_motor_test)
            _motor_test_delay = getattr(config_data.ezi_config, "motor_test_delay_sec", 2.0)
            # motor.clear_alarm()
            await asyncio.sleep(_motor_test_delay)
            await motor.servo_enable(True)
            await asyncio.sleep(_motor_test_delay)

            # Move to absolute position xxxx pulses
            # with speed xxxx pps
            await motor.move_single_axis_abs_pos(config_data.ezi_config.open_encoder_value, config_data.ezi_config.motor_speed)
            await asyncio.sleep(_motor_test_delay)
            await motor.move_single_axis_abs_pos(config_data.ezi_config.close_encoder_value, config_data.ezi_config.motor_speed)
            await asyncio.sleep(_motor_test_delay)

            await motor.servo_enable(False)



        # Publish ONLINE once. OFFLINE is retained by graceful shutdown or MQTT Last Will.
        await adapter.publish_connection(topic_name="connection", interval_sec=5, retain_msg=True, connection_state=ConnectionState.ONLINE) 

        # Run Adapter
        await adapter.run_adapter()

        # Loop forever: whenever ACS state is 'connecting',
        # run the full ACS handshake flow from the beginning.
        _loop_sleep = getattr(config_data.settings, "adapter_loop_sleep_sec", 1.0)
        while True:
            try:
                await asyncio.sleep(_loop_sleep)
            except Exception as e:
                print(f"ACS Adapter flow error: {e}")
                # Small delay before retry to avoid tight loop
                await asyncio.sleep(_loop_sleep)

    except asyncio.CancelledError:
        # Raised when asyncio.run() is interrupted (Ctrl+C)
        print("\nStopping the system...")
    except Exception as e:
        print(f"Error: {e}")
    finally:

        # send connectionState OFFLINE ( Retain True )
        if adapter is not None:
            release_charge_hold(adapter)
            await adapter.publish_connection(topic_name="connection", interval_sec=5, retain_msg=True , connection_state=ConnectionState.OFFLINE)
            adapter.disconnect_mqtt()


        # optional: cancel task on shutdown
        if vehicle_task is not None:
            vehicle_task.cancel()
        # if adapter_task is not None:
        #     adapter_task.cancel()


        if vehicle is not None:
            await disconnect_vehicle(vehicle)
        print("System stopped")



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Fallback for extra safety; main() already handles graceful shutdown
        print("\nKeyboard interrupt received.")
