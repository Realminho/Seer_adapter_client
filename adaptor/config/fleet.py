"""Shared loader for the robot fleet file (``config/robots.hcl``).

robots.hcl 한 파일을 main.py(``--robot``), run_multi.py, TUI registry가 모두
공유하도록 모은 모듈. 각 ``robot "<id>"`` 블록을 config override(섹션 구조)로
바꿔주고, id 검증(비어있음/중복)을 한곳에서 처리한다.

robots.hcl의 키 -> 의미:
    robot "<id>" -> vehicle.serial_number (MQTT 토픽 prefix, 고유 필수)
    vehicle_ip   -> vehicle.vehicle_ip
    vehicle_port -> vehicle.vehicle_port
    ezi_io       -> ezi.ezi_io
    ezi_motor    -> ezi.ezi_motor
    mqtt_host    -> mqtt_broker.host
    mqtt_port    -> mqtt_broker.port
    config       -> 인스턴스 전용 TOML 경로(override 아님; get_config(config_path=...))
    enabled      -> 이 기계에서 이 로봇을 띄울지(override 아님, 기본 true)
    simulator    -> 시뮬레이터 모드 여부(override 아님)
    extra_args   -> 그 밖에 main.py에 그대로 넘길 CLI 인자 배열

extension 블록을 로봇 안에 적으면 extensions.hcl의 같은 섹션 값을 이 로봇에서만
덮는다. 라벨과 키 이름은 extensions.hcl과 똑같다(config/extensions.py의
extension_sections()를 함께 쓴다):

    robot "HN-SH6-TR-002" {
      extension "pio" {
        # 안정 경로. scripts/list-serial-ports.sh 로 확인한다
        pio_serial_port = "/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0"
        vehicle_num     = "AMR002"
        advanced = { select_off_delay_sec = 0.5 }
      }
    }

get_config()이 extensions.hcl을 먼저 병합하고 이 override를 나중에 얹으므로,
extensions.hcl은 현장 공통 값 그대로 배포하고 기기마다 다른 값만 여기 적는다.
적지 않은 키는 extensions.hcl 값이 그대로 남는다.

``enabled = false``인 블록은 load_fleet()이 걸러내므로 아예 없는 것처럼 동작한다
(단일/다중 판정, run_multi 기동, TUI 목록, WebUI 검증이 모두 이 목록을 본다).
현장 로봇을 한 파일에 다 적어 두고 기계마다 자기 블록만 켜는 쓰임새다 — 그러면
robots.hcl도 공통본으로 배포할 수 있다. 지운 블록과 달리 이력이 남고, 오타로
막 지어낸 id가 조용히 도는 일도 없다.

경로 키(config 등)의 상대 경로는 robots.hcl 파일의 부모 디렉터리를 기준으로
푼다. 프로세스 CWD(서비스와 수동 실행이 다르다)에 의존하지 않기 위해서다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from config.extensions import ExtensionsError, extension_sections
from config.hcl import HclError, blocks, load_hcl

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FLEET_PATH = ADAPTER_ROOT / "config" / "robots.hcl"

#: robot 블록 안에서 extensions.hcl 섹션을 덮는 블록 이름.
_EXTENSION_KEY = "extension"

# robots.hcl 키 -> (config 섹션, config 키). override로 바뀌는 값만 둔다.
_OVERRIDE_KEYS = {
    "id": ("vehicle", "serial_number"),
    "vehicle_ip": ("vehicle", "vehicle_ip"),
    "vehicle_port": ("vehicle", "vehicle_port"),
    "ezi_io": ("ezi", "ezi_io"),
    "ezi_motor": ("ezi", "ezi_motor"),
    "mqtt_host": ("mqtt_broker", "host"),
    "mqtt_port": ("mqtt_broker", "port"),
}


#: robots.hcl에서 허용하는 키와 그 타입. 여기 없는 키는 오타로 본다.
#: HCL은 값 타입을 강제하지 않으므로 로더가 검증하지 않으면
#: ``simulator = "false"`` 같은 값이 truthy로 읽혀 실차가 시뮬레이터로 뜬다.
_FIELD_TYPES = {
    "vehicle_ip": "string",
    "ezi_io": "string",
    "ezi_motor": "string",
    "mqtt_host": "string",
    "config": "string",
    "extensions": "string",
    "recipes": "string",
    "vehicle_port": "port",
    "mqtt_port": "port",
    "enabled": "bool",
    "simulator": "bool",
    "extra_args": "string_list",
}


class FleetError(ValueError):
    """robots.hcl이 없거나 형식이 잘못됐을 때."""


def _check_field(rid: str, key: str, value: Any) -> None:
    """robot 블록의 키 하나를 스키마에 비춰 검증한다."""
    kind = _FIELD_TYPES.get(key)
    if kind is None:
        known = ", ".join(sorted(_FIELD_TYPES))
        raise FleetError(f"robot '{rid}': unknown field '{key}' (known: {known})")

    if kind == "string":
        if not isinstance(value, str):
            raise FleetError(
                f"robot '{rid}': {key} must be a quoted string, got "
                f"{type(value).__name__}"
            )
        return

    if kind == "port":
        # bool은 int의 서브클래스라 따로 걸러야 한다.
        if isinstance(value, bool) or not isinstance(value, int):
            raise FleetError(
                f"robot '{rid}': {key} must be an unquoted integer, got "
                f"{type(value).__name__}"
            )
        if not 1 <= value <= 65535:
            raise FleetError(f"robot '{rid}': {key} must be 1-65535, got {value}")
        return

    if kind == "bool":
        if not isinstance(value, bool):
            raise FleetError(
                f"robot '{rid}': {key} must be true or false without quotes, got "
                f"{type(value).__name__}"
            )
        return

    if kind == "string_list":
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise FleetError(
                f"robot '{rid}': {key} must be a list of quoted strings"
            )
        return


def load_fleet(
    path: Optional[Union[str, Path]] = None,
    include_disabled: bool = False,
) -> List[Dict[str, Any]]:
    """robots.hcl을 읽어 robot dict 목록을 돌려준다 (id 검증 포함).

    기본으로는 ``enabled = false``인 블록을 뺀다. 부르는 쪽이 전부 "지금 이
    기계에서 돌 로봇"을 뜻하는 목록으로 쓰기 때문이다. 파일을 있는 그대로
    보려면(사람이 준 id를 찾을 때, 편집기가 검증할 때) include_disabled=True.

    파일이 없거나, robot 블록이 없거나, id가 비었거나 중복이면 FleetError.
    켜진 블록이 하나도 없을 때도 FleetError — 조용히 아무것도 안 띄우면
    운영자는 서비스가 죽은 것과 구분할 수 없다.
    """
    fleet_path = Path(path) if path is not None else DEFAULT_FLEET_PATH
    try:
        data = load_hcl(fleet_path)
        entries = blocks(data, "robot")
    except HclError as exc:
        raise FleetError(
            # 무엇을 해야 하는지는 robots.toml 잔존 여부에 달려 있어 여기서는
            # 단정하지 않는다. 마이그레이션 중인 로봇에 예시를 복사하라고 하면
            # 그 로봇 identity의 유일한 사본을 템플릿 값으로 덮게 된다.
            f"{exc} (config/robots.hcl 이 필요합니다)"
        ) from exc

    if not entries:
        raise FleetError(f"no robot blocks in {fleet_path}")

    robots: List[Dict[str, Any]] = []
    seen = set()
    for index, (label, body) in enumerate(entries):
        rid = str(label).strip()
        if not rid:
            raise FleetError(f"robot #{index} has an empty id label")
        if rid in seen:
            raise FleetError(f"duplicate robot id: {rid}")
        seen.add(rid)
        robot = dict(body)
        for key, value in robot.items():
            if key == _EXTENSION_KEY:
                # 스칼라가 아니라 블록 목록이다. _extension_overrides가 검증한다.
                continue
            _check_field(rid, key, value)
        robot["id"] = rid
        # 잘못된 라벨을 여기서 잡아야 부팅이 멈춘다. 값을 쓰는 건
        # robot_overrides()지만, 그때까지 미루면 TUI 목록처럼 override를
        # 만들지 않는 경로에서는 오타가 끝까지 드러나지 않는다.
        _extension_overrides(rid, robot)
        robots.append(robot)

    if include_disabled:
        return robots

    enabled = [robot for robot in robots if robot.get("enabled", True)]
    if not enabled:
        raise FleetError(
            f"every robot block in {fleet_path} is disabled "
            f"({', '.join(r['id'] for r in robots)}); "
            "set enabled = true on the one this machine runs"
        )
    return enabled


def find_robot(fleet: List[Dict[str, Any]], robot_id: str) -> Dict[str, Any]:
    """fleet에서 id가 일치하는 robot을 찾는다. 없거나 꺼져 있으면 FleetError."""
    for robot in fleet:
        if robot.get("id") == robot_id:
            if not robot.get("enabled", True):
                # 블록이 파일에 그대로 있으므로 "not in fleet"이라고만 하면
                # 운영자는 없는 오타를 찾게 된다.
                raise FleetError(
                    f"robot '{robot_id}' is disabled in robots.hcl "
                    "(enabled = false); set enabled = true to run it"
                )
            return robot
    known = ", ".join(r.get("id", "?") for r in fleet) or "(none)"
    raise FleetError(f"robot id '{robot_id}' not in fleet. known ids: {known}")


def _extension_overrides(
    rid: str, robot: Mapping[str, Any]
) -> Dict[str, Dict[str, Any]]:
    """robot 블록 안의 extension 블록들을 config 섹션 override로 바꾼다.

    라벨 -> 섹션 짝과 advanced/motion_rules 승격은 extensions.hcl과 같은 함수를
    쓴다. 여기서 따로 구현하면 두 파일이 같은 이름을 다른 섹션으로 보내게 된다.
    """
    entries = robot.get(_EXTENSION_KEY)
    if entries is None:
        return {}
    try:
        parsed = blocks({_EXTENSION_KEY: entries}, _EXTENSION_KEY)
    except HclError as exc:
        raise FleetError(f"robot '{rid}': {exc}") from exc

    overrides: Dict[str, Dict[str, Any]] = {}
    seen = set()
    for label, body in parsed:
        if label in seen:
            raise FleetError(f"robot '{rid}': duplicate extension '{label}'")
        seen.add(label)
        try:
            sections = extension_sections(label, body, f"robot '{rid}'")
        except ExtensionsError as exc:
            raise FleetError(str(exc)) from exc
        for section, values in sections.items():
            overrides.setdefault(section, {}).update(values)
    return overrides


def robot_overrides(robot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """robot 한 항목을 config override(중첩 dict)로 변환한다.

    None인 값과 빈 섹션은 빼서 config.toml 기본값이 유지되게 한다.
    config/simulator/extra_args는 override가 아니라 별도로 다룬다.
    """
    # extension 블록을 먼저 깔고 전용 키를 나중에 얹는다. ezi_io처럼 두 곳에서
    # 같은 값을 가리킬 수 있는 키는 문서에 적힌 전용 키가 이기게 둔다.
    overrides = _extension_overrides(str(robot.get("id", "?")), robot)
    for key, (section, conf_key) in _OVERRIDE_KEYS.items():
        value = robot.get(key)
        if value is None:
            continue
        overrides.setdefault(section, {})[conf_key] = value
    return overrides


def resolve_robot_path(
    robot: Dict[str, Any],
    key: str,
    fleet_path: Optional[Union[str, Path]] = None,
) -> Optional[Path]:
    """robot 엔트리의 경로 키를 fleet 파일 부모 기준 절대 경로로 푼다."""
    raw = robot.get(key)
    if raw is None or str(raw).strip() == "":
        return None
    candidate = Path(str(raw))
    if candidate.is_absolute():
        return candidate
    base = Path(fleet_path) if fleet_path is not None else DEFAULT_FLEET_PATH
    return (base.parent / candidate).resolve()


def robot_ids(path: Optional[Union[str, Path]] = None) -> List[str]:
    """켜진 robot id 목록(순서 보존). 셸 스크립트에서 호출하기 좋게 분리."""
    return [robot["id"] for robot in load_fleet(path)]
