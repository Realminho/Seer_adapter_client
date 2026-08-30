"""Start the original repository WebUI as a drop-in SEER dashboard."""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path


# Keep the unchanged Adapter tree free of Python-generated __pycache__ files.
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


SEER_CLIENT_ROOT = Path(__file__).resolve().parent
DEFAULT_CREDENTIALS_FILE = SEER_CLIENT_ROOT / "webui_credentials.toml"


DEFAULT_MQTT_PORT = 1883
MOSQUITTO_START_TIMEOUT_SEC = 6.0


def _is_local_mqtt_host(host: str | None) -> bool:
    """Return True when an MQTT hostname points at this computer."""

    value = str(host or "").strip().lower()
    return value in {"127.0.0.1", "localhost", "::1"}


def _tcp_reachable(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _find_mosquitto_executable(explicit: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_path = os.getenv("SEER_MOSQUITTO_EXE", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    found = shutil.which("mosquitto") or shutil.which("mosquitto.exe")
    if found:
        candidates.append(Path(found))
    if os.name == "nt":
        for base_var in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.getenv(base_var, "").strip()
            if base:
                candidates.append(Path(base) / "mosquitto" / "mosquitto.exe")
    else:
        candidates.extend((Path("/usr/sbin/mosquitto"), Path("/usr/local/sbin/mosquitto")))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def _find_mosquitto_config(executable: Path, explicit: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_path = os.getenv("SEER_MOSQUITTO_CONFIG", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.append(executable.parent / "mosquitto.conf")
    if os.name != "nt":
        candidates.extend((Path("/etc/mosquitto/mosquitto.conf"), Path("/usr/local/etc/mosquitto/mosquitto.conf")))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def _tail_text(path: Path, max_chars: int = 2000) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return data[-max_chars:].strip()


def _mosquitto_autostart_log_path(platform_name: str | None = None) -> Path:
    """Return a Mosquitto log path that never lives inside the repository.

    A detached Mosquitto process keeps its stdout log open. Keeping that file
    under ``seer_client/runtime`` makes Windows refuse to delete or replace the
    project directory while the broker is running. Use a per-user state/log
    directory instead. ``SEER_MOSQUITTO_LOG_PATH`` can override the location.
    """

    explicit = os.getenv("SEER_MOSQUITTO_LOG_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if (platform_name or os.name) == "nt":
        base = (
            os.getenv("LOCALAPPDATA", "").strip()
            or os.getenv("TEMP", "").strip()
            or tempfile.gettempdir()
        )
        return Path(base) / "SEER Client" / "logs" / "mosquitto-autostart.log"
    state_home = os.getenv("XDG_STATE_HOME", "").strip()
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return base / "seer-client" / "mosquitto-autostart.log"


def ensure_local_mqtt_broker(
    host: str | None,
    port: int | None,
    *,
    enabled: bool = True,
    executable: Path | None = None,
    config: Path | None = None,
) -> bool:
    """Start a detached local Mosquitto broker when the requested port is closed.

    The broker is intentionally left running when the WebUI exits so other local
    tools can keep using the same MQTT service. Remote MQTT hosts are never
    started or modified.
    """

    if not enabled or not _is_local_mqtt_host(host):
        return False
    mqtt_host = str(host).strip()
    mqtt_port = int(port or DEFAULT_MQTT_PORT)
    if _tcp_reachable(mqtt_host, mqtt_port):
        print(f"MQTT broker already online: {mqtt_host}:{mqtt_port}")
        return False

    mosquitto = _find_mosquitto_executable(executable)
    if mosquitto is None:
        print(
            "WARNING: local MQTT is offline and mosquitto executable was not found. "
            "Install Mosquitto or set SEER_MOSQUITTO_EXE.",
            file=sys.stderr,
        )
        return False
    mosquitto_config = _find_mosquitto_config(mosquitto, config)

    log_path = _mosquitto_autostart_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [str(mosquitto)]
    if mosquitto_config is not None:
        command.extend(("-c", str(mosquitto_config)))
    command.append("-v")

    creationflags = 0
    popen_kwargs: dict[str, object] = {}
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        creationflags |= int(getattr(subprocess, "DETACHED_PROCESS", 0))
        creationflags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True

    try:
        with log_path.open("ab", buffering=0) as log_stream:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                close_fds=True,
                cwd=str(mosquitto.parent),
                **popen_kwargs,
            )
    except OSError as exc:
        print(f"WARNING: failed to auto-start Mosquitto: {exc}", file=sys.stderr)
        return False

    deadline = time.monotonic() + MOSQUITTO_START_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if _tcp_reachable(mqtt_host, mqtt_port):
            config_note = f" · config={mosquitto_config}" if mosquitto_config else ""
            print(f"MQTT broker auto-started: {mqtt_host}:{mqtt_port}{config_note}")
            print(f"MQTT log: {log_path}")
            return True
        time.sleep(0.15)

    detail = _tail_text(log_path)
    print(
        f"WARNING: Mosquitto was launched but {mqtt_host}:{mqtt_port} did not open "
        f"within {MOSQUITTO_START_TIMEOUT_SEC:.0f}s. Log: {log_path}",
        file=sys.stderr,
    )
    if detail:
        print(detail, file=sys.stderr)
    return False
REPO_ROOT = SEER_CLIENT_ROOT.parent
for source in (
    SEER_CLIENT_ROOT / "src",
    REPO_ROOT,
    REPO_ROOT / "amr-client-contract" / "src",
    REPO_ROOT / "jibot-client" / "src",
    REPO_ROOT / "adaptor",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from seer_client.fleet import SeerFleetError, SeerRobotConfig, load_seer_fleet  # pyright: ignore[reportMissingImports]  # noqa: E402
from seer_client.webui import (  # pyright: ignore[reportMissingImports]  # noqa: E402
    SeerFleetWebUiApplication,
    SeerWebUiApplication,
)


def load_webui_credentials(path: Path) -> dict[str, str]:
    """Load username/password from a local TOML file when it exists."""

    credential_path = Path(path).expanduser()
    if not credential_path.is_file():
        return {}
    try:
        with credential_path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read WebUI credentials file {credential_path}: {exc}") from exc
    section = document.get("webui", document)
    if not isinstance(section, dict):
        raise ValueError("WebUI credentials file [webui] must be a TOML table")
    result: dict[str, str] = {}
    for key in ("username", "password"):
        value = section.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"WebUI credentials {key} must be a string")
        result[key] = value
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Run the repository's unchanged WebUI with a drop-in SEER "
            "real/simulator adapter."
        )
    )
    parser.add_argument(
        "--fleet",
        "--fleet-config",
        type=Path,
        help=(
            "SEER fleet TOML with one or more [[robot]] entries. "
            "When set, per-robot --id/--vehicle-ip/--simulator options are ignored."
        ),
    )
    parser.add_argument("--simulator", action="store_true")
    parser.add_argument("--id", default="SEER-SIM-001")
    parser.add_argument(
        "--vehicle-ip",
        action="append",
        default=[],
        help=(
            "SEER controller IP. Repeat this option to show multiple AMRs in one WebUI. "
            "With one IP the original single-AMR control path is used unchanged."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9010)
    parser.add_argument(
        "--credentials-file",
        type=Path,
        default=Path(
            os.getenv("SEER_WEBUI_CREDENTIALS_FILE", str(DEFAULT_CREDENTIALS_FILE))
        ),
        help=(
            "TOML file containing [webui] username/password. "
            "CLI options take precedence over the file."
        ),
    )
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--mqtt-host")
    parser.add_argument("--mqtt-port", type=int)
    parser.add_argument(
        "--webui-vda-transport",
        choices=("local", "mqtt"),
        default=os.getenv("SEER_WEBUI_VDA_TRANSPORT", "mqtt").strip().lower() or "mqtt",
        help=(
            "WebUI command transport. 'local' (default) sends the same VDA5050 JSON "
            "to the Adapter over localhost TCP; 'mqtt' routes commands through the FMS broker."
        ),
    )
    parser.add_argument(
        "--no-auto-start-mqtt",
        action="store_true",
        help=(
            "Do not auto-start local Mosquitto when --mqtt-host is localhost/127.0.0.1. "
            "By default a missing local broker is started and left running."
        ),
    )
    parser.add_argument(
        "--mosquitto-exe",
        type=Path,
        help="Optional mosquitto executable path used for local MQTT auto-start.",
    )
    parser.add_argument(
        "--mosquitto-config",
        type=Path,
        help="Optional mosquitto.conf path used for local MQTT auto-start.",
    )
    parser.add_argument("--x", type=float, default=0.0)
    parser.add_argument("--y", type=float, default=0.0)
    parser.add_argument("--theta", type=float, default=0.0)
    parser.add_argument("--battery", type=float, default=70.0)
    parser.add_argument("--charging", action="store_true")
    parser.add_argument("--seer-state-port", type=int, default=19204)
    parser.add_argument("--seer-control-port", type=int, default=19205)
    parser.add_argument("--seer-task-port", type=int, default=19206)
    parser.add_argument("--seer-config-port", type=int, default=19207)
    parser.add_argument("--seer-other-port", type=int, default=19210)
    parser.add_argument("--seer-motor-names", default="")
    parser.add_argument(
        "--no-auto-start",
        action="store_true",
        help="Open WebUI with the adapter stopped; use its Start button later.",
    )
    args = parser.parse_args(argv)
    try:
        file_credentials = load_webui_credentials(args.credentials_file)
    except ValueError as exc:
        parser.error(str(exc))
    args.username = (
        args.username
        or file_credentials.get("username", "")
        or os.getenv("SEER_WEBUI_USERNAME", "")
        or "seer"
    )
    args.password = (
        args.password
        or file_credentials.get("password", "")
        or os.getenv("SEER_WEBUI_PASSWORD", "")
    )
    if not args.password and sys.stdin.isatty():
        args.password = getpass.getpass("SEER WebUI password (12+ characters): ")
    if not args.password:
        parser.error(
            "set [webui].password in webui_credentials.toml, "
            "set SEER_WEBUI_PASSWORD, or pass --password"
        )
    if args.fleet:
        try:
            args.robots = load_seer_fleet(args.fleet)
        except SeerFleetError as exc:
            parser.error(str(exc))
    elif not args.simulator and not args.vehicle_ip:
        parser.error("--vehicle-ip is required unless --simulator is used")
    if args.fleet and args.vehicle_ip:
        parser.error("--fleet and --vehicle-ip cannot be used together")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    ensure_local_mqtt_broker(
        args.mqtt_host,
        args.mqtt_port,
        enabled=not args.no_auto_start_mqtt,
        executable=args.mosquitto_exe,
        config=args.mosquitto_config,
    )
    if args.fleet:
        app = SeerFleetWebUiApplication(
            robots=args.robots,
            username=args.username,
            password=args.password,
            host=args.host,
            port=args.port,
            mqtt_host=args.mqtt_host,
            mqtt_port=args.mqtt_port,
            auto_start=not args.no_auto_start,
            webui_vda_transport=args.webui_vda_transport,
            fleet_path=args.fleet,
        )
        return app.serve()

    vehicle_ips = tuple(dict.fromkeys(str(ip).strip() for ip in args.vehicle_ip if str(ip).strip()))
    if len(vehicle_ips) > 1:
        robots = []
        for vehicle_ip in vehicle_ips:
            # Repeated --vehicle-ip mode is IP-centric: every AMR gets a stable,
            # unique VDA/runtime identity derived from its IP. This prevents the
            # first-listed vehicle from inheriting the generic single-AMR runtime
            # (map/order/action caches) when the list order changes.
            serial = f"{args.id}-IP-{vehicle_ip.replace('.', '-').replace(':', '-')}"
            robots.append(
                SeerRobotConfig(
                    serial=serial,
                    vehicle_ip=vehicle_ip,
                    simulator=False,
                    x=args.x,
                    y=args.y,
                    theta=args.theta,
                    battery=args.battery,
                    charging=args.charging,
                    state_port=args.seer_state_port,
                    control_port=args.seer_control_port,
                    task_port=args.seer_task_port,
                    config_port=args.seer_config_port,
                    other_port=args.seer_other_port,
                    motor_names=args.seer_motor_names,
                    mqtt_host=args.mqtt_host,
                    mqtt_port=args.mqtt_port,
                    auto_start=not args.no_auto_start,
                )
            )
        print("[SEER WEBUI AMR LIST] " + ", ".join(vehicle_ips))
        print(f"[SEER WEBUI SELECTED] {vehicle_ips[0]}")
        app = SeerFleetWebUiApplication(
            robots=tuple(robots),
            username=args.username,
            password=args.password,
            host=args.host,
            port=args.port,
            mqtt_host=args.mqtt_host,
            mqtt_port=args.mqtt_port,
            auto_start=not args.no_auto_start,
            webui_vda_transport=args.webui_vda_transport,
        )
        return app.serve()

    vehicle_ip = vehicle_ips[0] if vehicle_ips else ""
    app = SeerWebUiApplication(
        serial=args.id,
        simulator=args.simulator,
        vehicle_ip=vehicle_ip,
        username=args.username,
        password=args.password,
        host=args.host,
        port=args.port,
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        x=args.x,
        y=args.y,
        theta=args.theta,
        battery=args.battery,
        charging=args.charging,
        state_port=args.seer_state_port,
        control_port=args.seer_control_port,
        task_port=args.seer_task_port,
        config_port=args.seer_config_port,
        other_port=args.seer_other_port,
        motor_names=args.seer_motor_names,
        auto_start=not args.no_auto_start,
        webui_vda_transport=args.webui_vda_transport,
    )
    return app.serve()


if __name__ == "__main__":
    raise SystemExit(main())
