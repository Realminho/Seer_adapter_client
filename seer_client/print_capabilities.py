"""Print the machine-readable SEER/JIBOT capability decision table."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parent
for source in (ROOT / "src", ROOT.parent / "amr-client-contract" / "src", ROOT.parent / "adaptor"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from seer_client.capabilities import capability_report  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulator", action="store_true")
    parser.add_argument("--sound", action="store_true")
    parser.add_argument("--video", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--registry-action", action="append", default=[])
    parser.add_argument(
        "--runtime-adapter",
        action="store_true",
        help="load the shared Adapter and include the actual runtime ActionRegistry",
    )
    args = parser.parse_args(argv)
    registry_actions = list(args.registry_action)
    if args.runtime_adapter:
        from config.config import get_config
        import main as adapter_main
        from seer_client.bridge import (
            _disable_jibot_only_runtime_sources,
            install_into_adapter_main,
        )
        install_into_adapter_main(adapter_main)
        from adapter_jibot import Adapter
        config = get_config()
        _disable_jibot_only_runtime_sources(config)
        adapter = Adapter(config=config)
        registry_actions.extend(adapter._action_registry.action_types())
    report = capability_report(
        registry_actions=registry_actions,
        simulator=args.simulator,
        sound_enabled=args.sound,
        video_enabled=args.video,
    )
    if args.json:
        print(json.dumps([item.to_dict() for item in report], ensure_ascii=False, indent=2))
        return 0
    print("action_type\tstatus\tcondition\treason")
    for item in report:
        print(f"{item.action_type}\t{item.status}\t{item.condition}\t{item.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
