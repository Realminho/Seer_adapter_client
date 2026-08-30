"""SEER-local HCL templates and persistent runtime copies.

The shared Adapter source remains read-only.  Both the WebUI process and every
SEER Adapter child use the files created below ``seer_client/runtime`` (or a
caller supplied fleet runtime root), so Extensions/Recipes edits never touch
``adaptor/config``.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path


SEER_CLIENT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = SEER_CLIENT_ROOT / "config"
_ACTION_BLOCK = re.compile(
    r'(?ms)^action\s+"(?P<name>[A-Za-z][A-Za-z0-9_]*)"\s*\{.*?^\}\s*'
)



def _migrate_legacy_pio_ownership(text: str) -> str:
    """Migrate pre-2026-08-15 PIO station/channel ownership in SEER-local HCL.

    Shared Adapter config now requires station/channel to live on the facility
    blocks.  Existing SEER runtime files are intentionally persistent across
    upgrades, so migrate them in-place instead of making the WebUI fail to boot.
    """

    lines = text.splitlines()
    legacy_station = None
    legacy_channel = None
    output = []
    in_pio = False
    depth = 0

    for line in lines:
        stripped = line.strip()
        if not in_pio and stripped.startswith('extension "pio"') and "{" in line:
            in_pio = True
            depth = line.count("{") - line.count("}")
            output.append(line)
            continue
        if in_pio:
            if depth == 1 and "=" in stripped:
                key, value = (part.strip() for part in stripped.split("=", 1))
                if key == "station_id":
                    legacy_station = value
                    depth += line.count("{") - line.count("}")
                    if depth <= 0:
                        in_pio = False
                    continue
                if key == "channel":
                    legacy_channel = value
                    depth += line.count("{") - line.count("}")
                    if depth <= 0:
                        in_pio = False
                    continue
            depth += line.count("{") - line.count("}")
            output.append(line)
            if depth <= 0:
                in_pio = False
            continue
        output.append(line)

    # develop (14) makes these four extension blocks mandatory. Older SEER
    # runtime copies can predate that rule, while runtime files are intentionally
    # persistent across upgrades. Seed only missing blocks with neutral SEER-safe
    # values and preserve any existing operator-edited block.
    required_blocks = {
        "pio": [
            'extension "pio" {',
            '  pio_serial_port = ""',
            '  pio_baudrate = 38400',
            '  media = 0',
            '  port = 0',
            '  vehicle_num = ""',
            '}',
        ],
        "ezi": ['extension "ezi" {}'],
        "airshower": [
            'extension "airshower" {',
            '  pio_station_id = ""',
            '  channel = 0',
            '  failure = 0',
            '  occupied = 0',
            '  fun_working = 0',
            '  door_pin = [0, 1]',
            '  timeout_paring_requesting = 0',
            '  timeout_close_requesting = 0',
            '  timeout_open_requesting = 0',
            '  timeout_vacancy_waiting = 0',
            '  timeout_airflow_waiting = 0',
            '  poll_interval_sec = 0.2',
            '}',
        ],
        "elevator": [
            'extension "elevator" {',
            '  channel = 0',
            '  open_door_pin = 0',
            '  close_door_pin = 0',
            '  solid_on_second = 0',
            '  elevating_timing_second = 0',
            '  door_open_close_timing_second = 0',
            '  timeout_paring_requesting = 0',
            '  timeout_floor_requesting = 0',
            '  motion_rules = []',
            '}',
        ],
    }
    for block_name, block_lines in required_blocks.items():
        marker = f'extension "{block_name}"'
        if any(line.strip().startswith(marker) for line in output):
            continue
        if output and output[-1].strip():
            output.append("")
        output.extend(block_lines)

    def ensure_key(block_name: str, key: str, literal: str) -> None:
        start = None
        block_depth = 0
        end = None
        for index, current in enumerate(output):
            if start is None and current.strip().startswith(f'extension "{block_name}"') and "{" in current:
                start = index
                block_depth = current.count("{") - current.count("}")
                if block_depth <= 0:
                    output[index] = f'extension "{block_name}" {{'
                    output.insert(index + 1, "}")
                    end = index + 1
                    block_depth = 0
                continue
            if start is not None and end is None:
                block_depth += current.count("{") - current.count("}")
                if block_depth <= 0:
                    end = index
                    break
        if start is None or end is None:
            return
        key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=", re.MULTILINE)
        if key_pattern.search("\n".join(output[start : end + 1])):
            return
        output.insert(start + 1, f"  {key:<28} = {literal}")

    # These constructors still have required fields in develop (14), even when
    # their JIBOT action modules are disabled in a SEER process.
    for key, literal in (
        ("pio_serial_port", '""'),
        ("pio_baudrate", "38400"),
        ("media", "0"),
        ("port", "0"),
        ("vehicle_num", '""'),
    ):
        ensure_key("pio", key, literal)
    for key, literal in (
        ("pio_station_id", legacy_station or '""'),
        ("channel", legacy_channel or "0"),
        ("failure", "0"),
        ("occupied", "0"),
        ("fun_working", "0"),
        ("door_pin", "[0, 1]"),
        ("timeout_paring_requesting", "0"),
        ("timeout_close_requesting", "0"),
        ("timeout_open_requesting", "0"),
        ("timeout_vacancy_waiting", "0"),
        ("timeout_airflow_waiting", "0"),
        ("poll_interval_sec", "0.2"),
    ):
        ensure_key("airshower", key, literal)
    for key, literal in (
        ("channel", legacy_channel or "0"),
        ("open_door_pin", "0"),
        ("close_door_pin", "0"),
        ("solid_on_second", "0"),
        ("elevating_timing_second", "0"),
        ("door_open_close_timing_second", "0"),
        ("timeout_paring_requesting", "0"),
        ("timeout_floor_requesting", "0"),
        ("motion_rules", "[]"),
    ):
        ensure_key("elevator", key, literal)
    return "\n".join(output) + ("\n" if text.endswith("\n") else "")


@dataclass(frozen=True)
class SeerHclPaths:
    extensions: Path
    recipes: Path


def paths_for(directory: Path) -> SeerHclPaths:
    root = Path(directory)
    return SeerHclPaths(
        extensions=root / "extensions.hcl",
        recipes=root / "recipes.hcl",
    )


def ensure_hcl_files(directory: Path) -> SeerHclPaths:
    """Seed editable HCL files once and preserve all later WebUI edits."""

    targets = paths_for(Path(directory))
    targets.extensions.parent.mkdir(parents=True, exist_ok=True)
    for name, target in (
        ("extensions.hcl", targets.extensions),
        ("recipes.hcl", targets.recipes),
    ):
        if target.exists():
            # Existing WebUI edits are authoritative.  Only append newly
            # shipped SEER Action declarations that do not exist yet, so an
            # upgraded drop-in can execute new Block Builder programs without
            # resetting operator choices or hand-written recipes.
            if name == "extensions.hcl":
                template = TEMPLATE_DIR / name
                current = target.read_text(encoding="utf-8")
                migrated = _migrate_legacy_pio_ownership(current)
                if migrated != current:
                    target.write_text(migrated, encoding="utf-8")
                    current = migrated
                additions = []
                for match in _ACTION_BLOCK.finditer(
                    template.read_text(encoding="utf-8")
                ):
                    action_name = match.group("name")
                    if re.search(
                        r'^\s*action\s+"' + re.escape(action_name) + r'"\s*\{',
                        current,
                        re.MULTILINE,
                    ):
                        continue
                    additions.append(match.group(0).strip())
                if additions:
                    target.write_text(
                        current.rstrip() + "\n\n" + "\n\n".join(additions) + "\n",
                        encoding="utf-8",
                    )
            continue
        template = TEMPLATE_DIR / name
        if not template.is_file():
            raise FileNotFoundError(f"SEER HCL template is missing: {template}")
        shutil.copy2(template, target)
    return targets
