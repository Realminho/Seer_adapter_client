"""SEER-only fleet configuration for the drop-in multi-AMR WebUI."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple, Union

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # pyright: ignore[reportMissingImports]


_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_ALLOWED_KEYS = frozenset(
    {
        "id",
        "vehicle_ip",
        "simulator",
        "x",
        "y",
        "theta",
        "battery",
        "charging",
        "state_port",
        "control_port",
        "task_port",
        "config_port",
        "other_port",
        "motor_names",
        "mqtt_host",
        "mqtt_port",
        "auto_start",
    }
)


class SeerFleetError(ValueError):
    """Raised when a SEER fleet TOML file is missing or invalid."""


def _port(value: Any, *, name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise SeerFleetError(f"{name} must be an integer TCP port")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SeerFleetError(f"{name} must be an integer TCP port") from exc
    if not 1 <= parsed <= 65535:
        raise SeerFleetError(f"{name} must be between 1 and 65535")
    return parsed


def _number(value: Any, *, name: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise SeerFleetError(f"{name} must be numeric")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SeerFleetError(f"{name} must be numeric") from exc


def _boolean(value: Any, *, name: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise SeerFleetError(f"{name} must be true or false")
    return value


def _motor_names(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return ",".join(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return ",".join(item.strip() for item in value if item.strip())
    raise SeerFleetError("motor_names must be a string or an array of strings")


@dataclass(frozen=True)
class SeerRobotConfig:
    """Validated settings for one real or simulated SEER AMR."""

    serial: str
    vehicle_ip: str = ""
    simulator: bool = False
    x: float = 0.0
    y: float = 0.0
    theta: float = 0.0
    battery: float = 70.0
    charging: bool = False
    state_port: int = 19204
    control_port: int = 19205
    task_port: int = 19206
    config_port: int = 19207
    other_port: int = 19210
    motor_names: str = ""
    mqtt_host: Optional[str] = None
    mqtt_port: Optional[int] = None
    auto_start: bool = True

    @property
    def key(self) -> str:
        return f"seer:{self.serial}"

    def with_global_mqtt(
        self, host: Optional[str], port: Optional[int]
    ) -> "SeerRobotConfig":
        return replace(
            self,
            mqtt_host=self.mqtt_host if self.mqtt_host is not None else host,
            mqtt_port=self.mqtt_port if self.mqtt_port is not None else port,
        )

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any], *, index: int = 0
    ) -> "SeerRobotConfig":
        unknown = set(raw) - _ALLOWED_KEYS
        if unknown:
            names = ", ".join(sorted(unknown))
            raise SeerFleetError(f"robot #{index + 1} has unknown keys: {names}")

        serial = str(raw.get("id") or "").strip()
        if not serial:
            raise SeerFleetError(f"robot #{index + 1} has no id")
        if not _SAFE_ID.fullmatch(serial):
            raise SeerFleetError(
                f"robot id {serial!r} may contain only A-Z, a-z, 0-9, '.', '_' and '-'"
            )

        simulator = _boolean(
            raw.get("simulator"), name=f"{serial}.simulator", default=False
        )
        vehicle_ip = str(raw.get("vehicle_ip") or "").strip()
        if not simulator and not vehicle_ip:
            raise SeerFleetError(
                f"{serial}.vehicle_ip is required when simulator=false"
            )

        battery = _number(
            raw.get("battery"), name=f"{serial}.battery", default=70.0
        )
        if not 0.0 <= battery <= 100.0:
            raise SeerFleetError(f"{serial}.battery must be between 0 and 100")

        mqtt_port = raw.get("mqtt_port")
        return cls(
            serial=serial,
            vehicle_ip=vehicle_ip,
            simulator=simulator,
            x=_number(raw.get("x"), name=f"{serial}.x", default=0.0),
            y=_number(raw.get("y"), name=f"{serial}.y", default=0.0),
            theta=_number(
                raw.get("theta"), name=f"{serial}.theta", default=0.0
            ),
            battery=battery,
            charging=_boolean(
                raw.get("charging"), name=f"{serial}.charging", default=False
            ),
            state_port=_port(
                raw.get("state_port"), name=f"{serial}.state_port", default=19204
            ),
            control_port=_port(
                raw.get("control_port"),
                name=f"{serial}.control_port",
                default=19205,
            ),
            task_port=_port(
                raw.get("task_port"), name=f"{serial}.task_port", default=19206
            ),
            config_port=_port(
                raw.get("config_port"), name=f"{serial}.config_port", default=19207
            ),
            other_port=_port(
                raw.get("other_port"), name=f"{serial}.other_port", default=19210
            ),
            motor_names=_motor_names(raw.get("motor_names")),
            mqtt_host=(
                str(raw["mqtt_host"]).strip()
                if raw.get("mqtt_host") is not None
                else None
            ),
            mqtt_port=(
                _port(mqtt_port, name=f"{serial}.mqtt_port", default=1883)
                if mqtt_port is not None
                else None
            ),
            auto_start=_boolean(
                raw.get("auto_start"), name=f"{serial}.auto_start", default=True
            ),
        )


def validate_seer_fleet(
    robots: Sequence[SeerRobotConfig],
) -> Tuple[SeerRobotConfig, ...]:
    normalized = tuple(robots)
    if not normalized:
        raise SeerFleetError("SEER fleet must contain at least one [[robot]] entry")
    seen = set()
    for robot in normalized:
        if not _SAFE_ID.fullmatch(robot.serial):
            raise SeerFleetError(f"invalid robot id: {robot.serial!r}")
        if not robot.simulator and not robot.vehicle_ip.strip():
            raise SeerFleetError(
                f"{robot.serial}.vehicle_ip is required when simulator=false"
            )
        if not 0.0 <= float(robot.battery) <= 100.0:
            raise SeerFleetError(
                f"{robot.serial}.battery must be between 0 and 100"
            )
        for name in (
            "state_port",
            "control_port",
            "task_port",
            "config_port",
            "other_port",
        ):
            value = getattr(robot, name)
            if not 1 <= int(value) <= 65535:
                raise SeerFleetError(
                    f"{robot.serial}.{name} must be between 1 and 65535"
                )
        if robot.serial in seen:
            raise SeerFleetError(f"duplicate robot id: {robot.serial}")
        seen.add(robot.serial)
    return normalized


def load_seer_fleet(
    path: Union[str, Path],
) -> Tuple[SeerRobotConfig, ...]:
    """Load and validate ``[[robot]]`` entries from a SEER fleet TOML file."""

    fleet_path = Path(path)
    if not fleet_path.is_file():
        raise SeerFleetError(f"SEER fleet file not found: {fleet_path}")
    try:
        with fleet_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SeerFleetError(f"cannot read SEER fleet file {fleet_path}: {exc}") from exc
    raw_robots = data.get("robot")
    if not isinstance(raw_robots, list) or not raw_robots:
        raise SeerFleetError(f"no [[robot]] entries in {fleet_path}")
    robots = []
    for index, raw in enumerate(raw_robots):
        if not isinstance(raw, Mapping):
            raise SeerFleetError(f"robot #{index + 1} must be a TOML table")
        robots.append(SeerRobotConfig.from_mapping(raw, index=index))
    return validate_seer_fleet(robots)
