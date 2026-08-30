"""Interactive SEER direct-API and VDA5050 MQTT test console.

This script talks to either a physical controller through ``SeerAdapterClient``
or the in-process five-port TCP simulator through
``seer_simulator/SimulatedSEER``.  With ``--vda5050`` it instead represents a
small FMS: commands are sent to the running Adapter through MQTT and all raw
VDA5050 topic/JSON traffic is printed. The original adapter remains untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence


sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


SEER_CLIENT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SEER_CLIENT_ROOT.parent
for source in (
    SEER_CLIENT_ROOT / "src",
    REPO_ROOT,
    REPO_ROOT / "amr-client-contract" / "src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from seer_client.bridge import SeerAdapterClient  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.interactive_prompt import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    CompletionCatalog,
    InteractiveCommandPrompt,
)
from seer_client.vda5050_console import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    Vda5050Identity,
    Vda5050MqttConsole,
)
from seer_simulator import SimulatedSEER  # pyright: ignore[reportMissingImports]  # noqa: E402


HELP_TEXT = """Commands:
  help                              show this help
  status                            refresh and show normalized robot status
  loc                               show location response
  battery                           show battery response
  task                              show task response
  blocked                           show blocked state
  emergency                         show physical/driver/software emergency state
  emc                               set SEER software emergency switch
  emc_release                       release SEER software emergency switch
  map                               show map metadata (STATE 1300)
  map_download [map_name]           download full .smap body (CONFIG 4011)
  io                                show raw DI/DO response
  di <id>                           read one digital input
  do <id> <on|off>                  change one digital output
  motor <on|off>                    change all SEER_MOTOR_NAMES motors
  motor <name> <on|off>             change one named motor
  jack_load                         run JackLoad (Lift raise; DI2 OFF)
  jack_unload                       run JackUnLoadAndResetShelf (Lift lower; DI2 ON)
  goto <station_id>                 navigate to a landmark
  goto_route <point1> <point2> ...  navigate one designated API 3066 route
  goto_xyz <x> <y> <theta>          navigate to a pose
  free <x> <y> <theta>              free navigation to a pose
  drive <vx> <vy> <w>               open-loop velocity command
  translate <distance> <speed>      relative forward movement
  turn <angle> <angular_speed>      relative rotation
  stop | pause | resume | cancel    task control
  relocate <x> <y> <theta>          manual localization
  map_switch <map_name>             load another map
  soft_emergency <on|off>           set/release the SEER software emergency switch
  toggle_emergency                  toggle the SEER software emergency switch
  watch [count] [interval_sec]      print status repeatedly (defaults: 10, 1)
  wait <seconds>                    wait while background polling continues
  quit | exit                       disconnect and finish

Position and angle values use --position-unit and --orientation-unit.
Real-device write/motion commands require --allow-write.
"""


DIRECT_COMPLETION_CATALOG = CompletionCatalog(
    commands={
        "help": "show command help",
        "status": "show normalized robot status",
        "loc": "show localization",
        "battery": "show battery",
        "task": "show task status",
        "blocked": "show blocked state",
        "emergency": "show emergency state",
        "emc": "set software emergency",
        "emc_release": "release software emergency",
        "map": "show map metadata",
        "map_download": "download full current .smap",
        "io": "show all DI/DO",
        "di": "read one digital input",
        "do": "set one digital output",
        "motor": "enable or disable motor",
        "jack_load": "run jackDoMotor.py JackLoad (raise)",
        "jack_unload": "run jackDoMotor.py JackUnLoadAndResetShelf (lower)",
        "goto": "navigate to a named point",
        "goto_route": "navigate through one designated point route",
        "goto_xyz": "navigate to coordinates",
        "free": "free navigation to coordinates",
        "drive": "open-loop velocity command",
        "translate": "relative straight movement",
        "turn": "relative rotation",
        "stop": "stop motion",
        "pause": "pause task",
        "resume": "resume task",
        "cancel": "cancel task",
        "relocate": "set localization pose",
        "map_switch": "load another map",
        "soft_emergency": "set or release software emergency",
        "toggle_emergency": "toggle software emergency",
        "watch": "show status repeatedly",
        "wait": "wait while polling continues",
        "quit": "disconnect and finish",
        "exit": "disconnect and finish",
    },
    argument_choices={
        "di": {0: tuple(str(index) for index in range(24))},
        "do": {
            0: tuple(str(index) for index in range(24)),
            1: ("on", "off"),
        },
        "motor": {0: ("on", "off"), 1: ("on", "off")},
        "soft_emergency": {0: ("on", "off")},
        "watch": {0: ("1", "5", "10", "30"), 1: ("0.1", "0.5", "1")},
        "wait": {0: ("1", "5", "10", "30")},
    },
)


WRITE_COMMANDS = frozenset(
    {
        "do",
        "motor",
        "jack_load",
        "jack_unload",
        "goto",
        "goto_route",
        "goto_xyz",
        "free",
        "drive",
        "translate",
        "turn",
        "stop",
        "pause",
        "resume",
        "cancel",
        "relocate",
        "map_switch",
        "soft_emergency",
        "toggle_emergency",
        "emc",
        "emc_release",
    }
)


def parse_switch(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "on", "yes", "enable", "enabled"}:
        return True
    if normalized in {"0", "false", "off", "no", "disable", "disabled"}:
        return False
    raise ValueError(f"expected on/off, got {value!r}")


def require_args(command: str, args: Sequence[str], count: int) -> None:
    if len(args) != count:
        raise ValueError(f"{command} expects {count} argument(s), got {len(args)}")


class SeerManualTestConsole:
    """Parse human-readable commands and call one connected SeerClient."""

    def __init__(
        self,
        client: Any,
        *,
        allow_write: bool,
        output: Callable[[str], None] = print,
    ) -> None:
        self.client = client
        self.allow_write = bool(allow_write)
        self.output = output

    def _show(self, label: str, value: Any) -> None:
        self.output(
            f"[{label}] "
            + json.dumps(value, ensure_ascii=False, indent=2, default=str)
        )

    def _normalized_status(self) -> Mapping[str, Any]:
        return {
            "connected": self.client.is_connected(),
            "mode": self.client.mode,
            "status": self.client.status,
            "x": self.client.x,
            "y": self.client.y,
            "theta": self.client.th,
            "station": self.client.station,
            "battery_percent": self.client.battery,
            "charging": self.client.charging,
            "motor_enabled": self.client.motor_flag,
            "localization_score": self.client.localization_score,
            "blocked": bool(getattr(self.client, "_blocked", False)),
            "emergency": bool(getattr(self.client, "_emergency", False)),
            "soft_emergency": bool(getattr(self.client, "_soft_emergency", False)),
        }

    async def execute(self, line: str) -> bool:
        """Execute one command; return ``False`` for quit/exit."""

        tokens = shlex.split(line)
        if not tokens:
            return True
        command = tokens[0].strip().lower().replace("-", "_")
        args = tokens[1:]

        if command in {"quit", "exit"}:
            return False
        if command == "help":
            self.output(HELP_TEXT.rstrip())
            return True
        if command in WRITE_COMMANDS and not self.allow_write:
            raise PermissionError(
                "write/motion command blocked; restart a real-device session "
                "with --allow-write"
            )

        handler = getattr(self, f"_command_{command}", None)
        if handler is None:
            raise ValueError(f"unknown command: {command!r}; enter 'help'")
        await handler(args)
        return True

    async def _command_status(self, args: Sequence[str]) -> None:
        require_args("status", args, 0)
        await self.client.get_robot_info()
        self._show("STATUS", self._normalized_status())

    async def _command_loc(self, args: Sequence[str]) -> None:
        require_args("loc", args, 0)
        self._show("LOCATION", await self.client.get_localization_info())

    async def _command_battery(self, args: Sequence[str]) -> None:
        require_args("battery", args, 0)
        self._show("BATTERY", await self.client.get_battery_info())

    async def _command_task(self, args: Sequence[str]) -> None:
        require_args("task", args, 0)
        self._show("TASK", await self.client.get_task_status())

    async def _command_blocked(self, args: Sequence[str]) -> None:
        require_args("blocked", args, 0)
        self._show("BLOCKED", await self.client.get_blocked())

    async def _command_emergency(self, args: Sequence[str]) -> None:
        require_args("emergency", args, 0)
        self._show("EMERGENCY", await self.client.get_emergency_state())

    async def _command_emc(self, args: Sequence[str]) -> None:
        require_args("emc", args, 0)
        enabled = await self.client.set_soft_emergency(True)
        self._show("EMC", {"enabled": enabled, "result": "active"})

    async def _command_emc_release(self, args: Sequence[str]) -> None:
        require_args("emc_release", args, 0)
        enabled = await self.client.set_soft_emergency(False)
        self._show("EMC_RELEASE", {"enabled": enabled, "result": "released"})

    async def _command_map(self, args: Sequence[str]) -> None:
        require_args("map", args, 0)
        self._show("MAP", await self.client.get_map_info())

    async def _command_map_download(self, args: Sequence[str]) -> None:
        if len(args) > 1:
            raise ValueError("usage: map_download [map_name]")
        map_name = args[0] if args else None
        started = time.monotonic()
        raw = await self.client.download_map(map_name)
        header = raw.get("header") if isinstance(raw, Mapping) else None
        points = raw.get("advancedPointList") if isinstance(raw, Mapping) else None
        lines = raw.get("advancedLineList") if isinstance(raw, Mapping) else None
        self._show(
            "MAP_DOWNLOAD",
            {
                "map_name": (header or {}).get("mapName") if isinstance(header, Mapping) else map_name,
                "advanced_points": len(points) if isinstance(points, list) else 0,
                "advanced_lines": len(lines) if isinstance(lines, list) else 0,
                "elapsed_sec": round(time.monotonic() - started, 3),
                "result": "ok",
            },
        )

    async def _command_io(self, args: Sequence[str]) -> None:
        require_args("io", args, 0)
        self._show("IO", await self.client.send_command("query_io"))

    async def _command_di(self, args: Sequence[str]) -> None:
        require_args("di", args, 1)
        di_id = int(args[0])
        self._show("DI", {"id": di_id, "status": await self.client.read_di(di_id)})

    async def _command_do(self, args: Sequence[str]) -> None:
        require_args("do", args, 2)
        do_id, enabled = int(args[0]), parse_switch(args[1])
        await self.client.set_do(do_id, enabled)
        self._show("DO", {"id": do_id, "status": enabled, "result": "ok"})

    async def _command_motor(self, args: Sequence[str]) -> None:
        if len(args) == 1:
            enabled = parse_switch(args[0])
            if enabled:
                await self.client.enable_motor()
            else:
                await self.client.disable_motor()
            self._show("MOTOR", {"all": True, "enabled": enabled, "result": "ok"})
            return
        if len(args) == 2:
            name, enabled = args[0], parse_switch(args[1])
            await self.client.set_motor(name, enabled)
            self._show("MOTOR", {"name": name, "enabled": enabled, "result": "ok"})
            return
        raise ValueError("motor expects: motor <on|off> OR motor <name> <on|off>")

    async def _command_jack_load(self, args: Sequence[str]) -> None:
        require_args("jack_load", args, 0)
        await self.client.jack_load()
        di2 = await self.client.read_di(2)
        self._show("JACK_LOAD", {"result": "accepted", "di2_down": di2})

    async def _command_jack_unload(self, args: Sequence[str]) -> None:
        require_args("jack_unload", args, 0)
        await self.client.jack_unload()
        di2 = await self.client.read_di(2)
        self._show("JACK_UNLOAD", {"result": "accepted", "di2_down": di2})

    async def _command_goto(self, args: Sequence[str]) -> None:
        require_args("goto", args, 1)
        await self.client.goto_point(args[0])
        self._show("GOTO", {"station": args[0], "result": "accepted"})

    async def _command_goto_route(self, args: Sequence[str]) -> None:
        if len(args) < 2:
            raise ValueError("goto_route expects at least two point ids")
        await self.client.path_navigation_route(tuple(args), task_id="")
        self._show(
            "GOTO_ROUTE",
            {"route_points": list(args), "api": 3066, "result": "accepted"},
        )

    async def _command_goto_xyz(self, args: Sequence[str]) -> None:
        require_args("goto_xyz", args, 3)
        x, y, theta = map(float, args)
        await self.client.goto_xyz(x, y, theta)
        self._show("GOTO_XYZ", {"x": x, "y": y, "theta": theta, "result": "accepted"})

    async def _command_free(self, args: Sequence[str]) -> None:
        require_args("free", args, 3)
        x, y, theta = map(float, args)
        await self.client.free_nav(x, y, theta)
        self._show("FREE", {"x": x, "y": y, "theta": theta, "result": "accepted"})

    async def _command_drive(self, args: Sequence[str]) -> None:
        require_args("drive", args, 3)
        vx, vy, w = map(float, args)
        await self.client.drive(vx, w, 0.0, vy)
        self._show("DRIVE", {"vx": vx, "vy": vy, "w": w, "result": "accepted"})

    async def _command_translate(self, args: Sequence[str]) -> None:
        require_args("translate", args, 2)
        distance, speed = map(float, args)
        await self.client.move_distance(distance, speed)
        self._show("TRANSLATE", {"distance": distance, "speed": speed, "result": "accepted"})

    async def _command_turn(self, args: Sequence[str]) -> None:
        require_args("turn", args, 2)
        angle, speed = map(float, args)
        await self.client.turn(angle, speed)
        self._show("TURN", {"angle": angle, "speed": speed, "result": "accepted"})

    async def _command_stop(self, args: Sequence[str]) -> None:
        require_args("stop", args, 0)
        await self.client.stop_motion()
        self._show("STOP", {"result": "ok"})

    async def _command_pause(self, args: Sequence[str]) -> None:
        require_args("pause", args, 0)
        await self.client.pause_navigation()
        self._show("PAUSE", {"result": "ok"})

    async def _command_resume(self, args: Sequence[str]) -> None:
        require_args("resume", args, 0)
        await self.client.resume_navigation()
        self._show("RESUME", {"result": "ok"})

    async def _command_cancel(self, args: Sequence[str]) -> None:
        require_args("cancel", args, 0)
        await self.client.cancel_navigation()
        self._show("CANCEL", {"result": "ok"})

    async def _command_relocate(self, args: Sequence[str]) -> None:
        require_args("relocate", args, 3)
        x, y, theta = map(float, args)
        await self.client.relocation(auto=False, x=x, y=y, angle=theta)
        self._show("RELOCATE", {"x": x, "y": y, "theta": theta, "result": "ok"})

    async def _command_map_switch(self, args: Sequence[str]) -> None:
        require_args("map_switch", args, 1)
        await self.client.map_switch(args[0])
        self._show("MAP_SWITCH", {"map": args[0], "result": "ok"})

    async def _command_soft_emergency(self, args: Sequence[str]) -> None:
        require_args("soft_emergency", args, 1)
        enabled = parse_switch(args[0])
        result = await self.client.set_soft_emergency(enabled)
        self._show("SOFT_EMERGENCY", {"enabled": enabled, "response": result})

    async def _command_toggle_emergency(self, args: Sequence[str]) -> None:
        require_args("toggle_emergency", args, 0)
        enabled = await self.client.toggle_soft_emergency()
        self._show("SOFT_EMERGENCY", {"enabled": enabled, "result": "ok"})

    async def _command_wait(self, args: Sequence[str]) -> None:
        require_args("wait", args, 1)
        seconds = max(0.0, float(args[0]))
        await asyncio.sleep(seconds)
        self._show("WAIT", {"seconds": seconds, "result": "done"})

    async def _command_watch(self, args: Sequence[str]) -> None:
        if len(args) > 2:
            raise ValueError("watch expects: watch [count] [interval_sec]")
        count = int(args[0]) if args else 10
        interval = float(args[1]) if len(args) == 2 else 1.0
        if count < 1 or interval < 0:
            raise ValueError("watch count must be >= 1 and interval must be >= 0")
        for index in range(count):
            await self.client.get_robot_info()
            self._show(f"STATUS {index + 1}/{count}", self._normalized_status())
            if index + 1 < count:
                await asyncio.sleep(interval)

    async def repl(self) -> None:
        prompt = InteractiveCommandPrompt(DIRECT_COMPLETION_CATALOG)
        self.output(
            "SEER manual test console connected. Press Tab twice for command "
            "candidates; enter 'help' for details."
        )
        if not prompt.completion_enabled:
            self.output(
                "[TAB COMPLETION DISABLED] This input is not attached to a Windows "
                f"console: {prompt.unavailable_reason}"
            )
        while True:
            try:
                line = await prompt.read("seer> ")
                if not await self.execute(line):
                    return
            except EOFError:
                return
            except (ValueError, PermissionError, RuntimeError) as exc:
                self.output(f"[ERROR] {exc}")
            except Exception as exc:
                self.output(f"[SEER ERROR] {type(exc).__name__}: {exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Interactive SEER direct-API or VDA5050 MQTT test console"
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--simulator", action="store_true", help="use local SEER TCP simulator")
    target.add_argument("--ip", help="physical SEER controller IP address")
    target.add_argument(
        "--vda5050",
        action="store_true",
        help="act as a test FMS through MQTT; an Adapter must already be running",
    )
    parser.add_argument("--id", help="Adapter serial number (required with --vda5050)")
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--mqtt-user")
    parser.add_argument("--mqtt-password")
    parser.add_argument("--mqtt-qos", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--vda-interface", default="amr")
    parser.add_argument("--vda-version", default="v3", help="MQTT topic version segment")
    parser.add_argument("--vda-full-version", default="3.0.0", help="message header version")
    parser.add_argument("--manufacturer", default="seer")
    parser.add_argument("--mqtt-connect-timeout", type=float, default=5.0)
    parser.add_argument(
        "--vda-map-cache",
        help=(
            "normalized seer-map.json used to fill VDA5050 nodePosition; "
            "auto-detects runtime/<id>/seer-map.json or runtime/seer-map.json"
        ),
    )
    parser.add_argument("--allow-write", action="store_true", help="allow I/O and motion writes on a physical robot")
    parser.add_argument("--position-unit", choices=("m", "cm", "mm"), default="mm")
    parser.add_argument("--orientation-unit", choices=("rad", "deg"), default="deg")
    parser.add_argument("--x", type=float, default=0.0, help="simulator initial x")
    parser.add_argument("--y", type=float, default=0.0, help="simulator initial y")
    parser.add_argument("--theta", type=float, default=0.0, help="simulator initial heading")
    parser.add_argument("--battery", type=float, default=80.0, help="simulator initial battery percent")
    parser.add_argument("--charging", action="store_true", help="simulator starts charging")
    parser.add_argument("--map", dest="map_id", default="manual-test")
    parser.add_argument("--motor-names", help="comma-separated exact vendor motor names")
    parser.add_argument("--state-port", type=int, help="override STATE port (default 19204)")
    parser.add_argument("--control-port", type=int, help="override CONTROL port (default 19205)")
    parser.add_argument("--task-port", type=int, help="override TASK port (default 19206)")
    parser.add_argument("--config-port", type=int, help="override CONFIG port (default 19207)")
    parser.add_argument("--other-port", type=int, help="override OTHER port (default 19210)")
    parser.add_argument(
        "--command",
        action="append",
        default=[],
        help="run one command non-interactively; may be repeated",
    )
    return parser


def _set_process_options(args: argparse.Namespace) -> None:
    values = {
        "SEER_STATE_PORT": args.state_port,
        "SEER_CONTROL_PORT": args.control_port,
        "SEER_TASK_PORT": args.task_port,
        "SEER_CONFIG_PORT": args.config_port,
        "SEER_OTHER_PORT": args.other_port,
        "SEER_MOTOR_NAMES": args.motor_names,
    }
    for name, value in values.items():
        if value is not None:
            os.environ[name] = str(value)


def _adapter_config(args: argparse.Namespace) -> Any:
    return SimpleNamespace(
        factsheet=SimpleNamespace(
            coordinate_unit_position=args.position_unit,
            coordinate_unit_orientation=args.orientation_unit,
        ),
        settings=SimpleNamespace(map_id=args.map_id),
        bms_ros=SimpleNamespace(enabled=True),
    )


def _vda_map_cache_path(args: argparse.Namespace) -> Optional[Path]:
    explicit = str(getattr(args, "vda_map_cache", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    serial = str(getattr(args, "id", "") or "").strip()
    candidates = []
    if serial:
        candidates.append(SEER_CLIENT_ROOT / "runtime" / serial / "seer-map.json")
    candidates.append(SEER_CLIENT_ROOT / "runtime" / "seer-map.json")
    return next((candidate for candidate in candidates if candidate.is_file()), None)


async def run(args: argparse.Namespace) -> int:
    if args.vda5050:
        if not str(args.id or "").strip():
            print("[ARGUMENT ERROR] --id is required with --vda5050")
            return 2
        identity = Vda5050Identity(
            serial_number=str(args.id).strip(),
            manufacturer=str(args.manufacturer),
            interface=str(args.vda_interface),
            topic_version=str(args.vda_version),
            message_version=str(args.vda_full_version),
        )
        console = Vda5050MqttConsole(
            identity,
            host=args.mqtt_host,
            port=args.mqtt_port,
            username=args.mqtt_user,
            password=args.mqtt_password,
            qos=args.mqtt_qos,
            allow_write=args.allow_write,
            map_cache_path=_vda_map_cache_path(args),
            position_unit=args.position_unit,
            orientation_unit=args.orientation_unit,
            map_id=args.map_id,
        )
        try:
            console.connect(timeout=args.mqtt_connect_timeout)
            if console.map_cache_path is not None:
                print(f"[VDA5050 ORDER MAP] {console.map_cache_path}")
            else:
                print(
                    "[VDA5050 ORDER MAP] no seer-map.json found; named-node orders "
                    "will omit nodePosition unless supplied by file/raw JSON"
                )
            if not args.allow_write:
                print(
                    "VDA5050 read-only mode: topic monitoring, status, and factsheet "
                    "are enabled. Add --allow-write for motion/I/O/order commands."
                )
            if args.command:
                for command in args.command:
                    print(f"vda5050> {command}")
                    try:
                        if not await console.execute(command):
                            break
                    except Exception as exc:
                        print(f"[ERROR] {type(exc).__name__}: {exc}")
                        return 1
            else:
                await console.repl()
            return 0
        except (ConnectionError, OSError, TimeoutError) as exc:
            print(f"[MQTT CONNECTION ERROR] {type(exc).__name__}: {exc}")
            return 2
        finally:
            console.close()
            print("VDA5050 MQTT disconnected.")

    _set_process_options(args)
    config = _adapter_config(args)
    if args.simulator:
        client = SimulatedSEER(
            config=config,
            initial_position={"x": args.x, "y": args.y, "theta": args.theta},
            initial_battery=args.battery,
            initial_charging=args.charging,
        )
        target_name = "local simulator"
    else:
        client = SeerAdapterClient(args.ip, config=config)
        target_name = str(args.ip)

    print(f"Connecting to {target_name} ...")
    try:
        await client.connect_socket()
        await client.connect()
        console = SeerManualTestConsole(
            client,
            allow_write=bool(args.simulator or args.allow_write),
        )
        print(
            f"Connected. Units: position={args.position_unit}, "
            f"orientation={args.orientation_unit}."
        )
        if not args.simulator and not args.allow_write:
            print("Read-only real-device mode. Restart with --allow-write for I/O or motion commands.")
        if args.command:
            for command in args.command:
                print(f"seer> {command}")
                try:
                    if not await console.execute(command):
                        break
                except Exception as exc:
                    print(f"[ERROR] {type(exc).__name__}: {exc}")
                    return 1
        else:
            await console.repl()
        return 0
    except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
        print(f"[CONNECTION ERROR] {type(exc).__name__}: {exc}")
        return 2
    finally:
        await client.disconnect()
        print("Disconnected.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
