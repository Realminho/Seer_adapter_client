#!/usr/bin/env python3
"""Validate one robot's complete extension/recipe configuration without I/O."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTOR_ROOT = REPO_ROOT / "adaptor"
sys.path.insert(0, str(ADAPTOR_ROOT))

from config.config import get_config  # noqa: E402
from config.fleet import (  # noqa: E402
    find_robot,
    load_fleet,
    resolve_robot_path,
    robot_overrides,
)
from core.action_modules import first_party_action_specs  # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate HCL extension/recipe config without opening hardware"
    )
    parser.add_argument(
        "--robots",
        type=Path,
        default=ADAPTOR_ROOT / "config" / "robots.hcl",
    )
    parser.add_argument("--robot", required=True, help="robot block label")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    robots_path = args.robots.resolve()
    # 사람이 준 id다. 꺼진 블록도 읽어서 "꺼져 있다"라고 말해 준다.
    robot = find_robot(load_fleet(robots_path, include_disabled=True), args.robot)
    config_path = resolve_robot_path(robot, "config", robots_path)
    extensions_path = resolve_robot_path(robot, "extensions", robots_path)
    recipes_path = resolve_robot_path(robot, "recipes", robots_path)
    config = get_config(
        config_path=config_path,
        overrides=robot_overrides(robot),
        extensions_path=extensions_path,
        recipes_path=recipes_path,
    )
    specs = first_party_action_specs(config)
    recipes = [recipe.action_type for recipe in config.recipes if recipe.enabled]
    print(f"OK robot={args.robot}")
    print(f"  config={config_path or 'default'}")
    print(f"  extensions={extensions_path or 'default'}")
    print(f"  recipes={recipes_path or 'default/none'}")
    print(f"  enabled recipes={', '.join(recipes) if recipes else '(none)'}")
    print(f"  registered actions={len(specs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
