"""``python -m web`` entry point. flat 레이아웃: adaptor/를 sys.path에 넣고 run()."""
import sys
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from web.main import run

if __name__ == "__main__":
    raise SystemExit(run())
