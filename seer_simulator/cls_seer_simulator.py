"""In-process SEER AMR simulator used by the existing VDA5050 adapter.

The adapter historically imports ``cls_jibot_simulator.SimulatedJIBOT`` when
``--simulator`` is selected.  ``seer_client.bridge`` installs this class under
that legacy import name at runtime, so the original adapter can stay shared by
JIBOT and SEER while the actual simulator still speaks SEER's five-port TCP
protocol.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


# A direct simulator import can pull in the unchanged Adapter sources. Keep
# bytecode generation from modifying directories outside the SEER packages.
sys.dont_write_bytecode = True


SEER_SIMULATOR_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SEER_SIMULATOR_ROOT.parent
for source in (
    REPO_ROOT,
    REPO_ROOT / "seer_client" / "src",
    REPO_ROOT / "amr-client-contract" / "src",
    REPO_ROOT / "jibot-client" / "src",
    REPO_ROOT / "adaptor",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from seer_client.bridge import SeerSimulatedAdapterClient  # pyright: ignore[reportMissingImports]  # noqa: E402


class SimulatedSEER(SeerSimulatedAdapterClient):
    """SEER simulator with the constructor and methods expected by Adapter.

    ``SeerSimulatedAdapterClient`` starts a stateful local SEER controller on
    five loopback TCP ports.  The inherited real ``SeerClient`` connects to
    those ports, which means simulation covers framing, routing, polling and
    state conversion and map download instead of bypassing the protocol layer.

    The simulator intentionally does not define a second FMS command format.
    VDA5050 ``order``/``instantActions`` are parsed by the same unchanged Adapter
    used for a real SEER, then arrive here as normal SEER southbound operations.
    """

    vendor = "seer"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.is_simulator = True


# Compatibility alias used only at the unmodified adapter's historical import
# boundary.  The object itself is still SimulatedSEER and uses SEER TCP.
SimulatedJIBOT = SimulatedSEER
