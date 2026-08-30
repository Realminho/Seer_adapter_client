"""Print the tail of a SEER drop-in log for the original WebUI log page."""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--lines", type=int, default=200)
    args = parser.parse_args()
    path = Path(args.path)
    if not path.exists():
        print("(no SEER adapter log yet)")
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in deque(stream, maxlen=max(1, args.lines)):
            print(line, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

