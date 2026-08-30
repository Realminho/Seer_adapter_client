"""Resolve a systemd instance name to (vendor, launcher-args) for run-adapter.sh.

amr-adaptor.service(기본) 또는 amr-adaptor@<instance>.service의 <instance>를
받아 어느 벤더로/어떤 인자로 띄울지 결정한다. main.py / main_hexplorer.py는
그대로 두고, 이 결과로 run-main.sh / run-hexplorer.sh를 고른다.

해석 우선순위:
    1) instance 없음 + robots.hcl 1개     -> ("jibot", ["--robots", path, "--robot", id])
    2) instance 없음 + robots.hcl 2개 이상 -> ("jibot-multi", ["--robots", path])
    3) [[adapter.instances]] name 일치     -> 그 vendor, config 있으면 --config <path>
    4) 그 외(robots.hcl robot id 포함)     -> ("jibot", ["--robots", path, "--robot", <instance>])
"""

from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

import sys
from pathlib import Path
from typing import List, Optional, Tuple

from config.fleet import DEFAULT_FLEET_PATH, FleetError, load_fleet
from config.hcl import HclError

_CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"
_ADAPTER_ROOT = Path(__file__).resolve().parent.parent


def _rel(path: Path) -> str:
    """Path as an operator would type it — they are cd'd into the adapter dir."""
    try:
        return str(path.relative_to(_ADAPTER_ROOT))
    except ValueError:
        return str(path)


def _python_bin() -> str:
    """Interpreter to put in printed commands, resolved like run-adapter.sh.

    Robots differ: some have .venv, some only venvJIBOT, some neither. Naming a
    fixed one turns recovery advice into a second failure, so probe the same
    order run-adapter.sh does and fall back to python3.
    """
    for candidate in (".venv/bin/python", "venvJIBOT/bin/python"):
        if (_ADAPTER_ROOT / candidate).is_file():
            return candidate
    return "python3"


def _fleet_help(fleet_path: Path) -> List[str]:
    """Actionable next step for an unusable robots.hcl, chosen by what is there.

    A robot mid-migration still holds robots.toml, and that is the only copy of
    its identity/IPs — converting it is right, while copying the example would
    overwrite that robot with template values.
    """
    legacy = fleet_path.with_name("robots.toml")
    if legacy.is_file():
        return [
            f"{_rel(legacy)} 이 남아 있습니다 — 이 로봇 identity의 유일한 사본입니다.",
            f"변환하세요 (cd {_ADAPTER_ROOT} 후):",
            f"  {_python_bin()} scripts/convert-robots-toml-to-hcl.py \\",
            f"      {_rel(legacy)} -o {_rel(fleet_path)}",
            "변환 후 vehicle_ip / ezi_io / mqtt_host 가 이 로봇 실제 값인지 확인하세요.",
        ]
    return [
        "robots.toml 도 없습니다. 예시를 복사해 이 로봇 값으로 채우세요:",
        f"  cp config/robots.hcl.example {_rel(fleet_path)}",
        "robot 블록 라벨이 이 로봇의 id이며 VDA5050 serialNumber가 됩니다.",
        # The example ships two robot blocks to document the multi-robot layout,
        # with the second one disabled so a verbatim copy starts a single
        # adapter. Enabling both puts two adapters on one onboard PC, dialling
        # the example's placeholder IPs. Say so here: this text is the
        # operator's only prompt at the moment they copy it.
        "예시에는 robot 블록이 2개 있고 두 번째는 enabled = false로 꺼져 있습니다.",
        "이 로봇 블록을 켜고 나머지는 enabled = false로 두세요 —",
        "켜진 블록이 2개 이상이면 run_multi.py로 adapter가 여러 개 뜹니다.",
        "vehicle_ip / ezi_io / ezi_motor 는 예시의 자리표시자 주소이므로 반드시 실제 값으로 바꾸세요.",
    ]


def _adapter_table(config_path: Optional[str]) -> dict:
    path = Path(config_path) if config_path else _CONFIG_PATH
    with open(path, "rb") as fh:
        return tomllib.load(fh).get("adapter", {})


def resolve_dispatch(
    instance: Optional[str],
    *,
    config_path: Optional[str] = None,
    robots_path: Optional[str] = None,
) -> Tuple[str, List[str]]:
    fleet_path = str(Path(robots_path) if robots_path else DEFAULT_FLEET_PATH)
    adapter = _adapter_table(config_path)
    if not instance:
        robots = load_fleet(fleet_path)
        if len(robots) == 1:
            return "jibot", ["--robots", fleet_path, "--robot", robots[0]["id"]]
        return "jibot-multi", ["--robots", fleet_path]

    for inst in adapter.get("instances", []):
        if inst.get("name") == instance:
            vendor = inst.get("vendor", "jibot")
            args = ["--config", inst["config"]] if inst.get("config") else []
            return vendor, args

    # 명시 인스턴스가 아니면 jibot fleet의 robot id로 간주한다.
    return "jibot", ["--robots", fleet_path, "--robot", instance]


def _main(argv: List[str]) -> int:
    instance = argv[1] if len(argv) > 1 and argv[1] else None
    fleet_path = Path(argv[2]) if len(argv) > 2 and argv[2] else DEFAULT_FLEET_PATH
    try:
        vendor, args = resolve_dispatch(instance, robots_path=str(fleet_path))
    except (FleetError, HclError) as exc:
        # stdout is parsed by run-adapter.sh, so diagnostics go to stderr.
        # A traceback is worse than useless here: systemd restarts every few
        # seconds, so the journal fills with stack frames and the one line an
        # operator can act on scrolls away. Print that line instead and let the
        # non-zero exit still fail the unit.
        for line in (
            "기동할 수 없습니다 — robots.hcl 을 읽지 못했습니다.",
            f"  {exc}",
            *_fleet_help(fleet_path),
        ):
            print(f"[adapter-dispatch] {line}", file=sys.stderr)
        return 1
    print(vendor)
    for arg in args:
        print(arg)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
