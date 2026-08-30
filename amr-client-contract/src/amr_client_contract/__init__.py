"""amr-client-contract: the common adaptor <-> AMR-client interface layer.

amr-client-contract: 공용 어댑터 <-> AMR 클라이언트 인터페이스 계층.

Public surface:
  - Protocols (structural interfaces, ``[ADAPTOR-CONTRACT]``):
      AmrConnection, AmrMotion, AmrStateReader, AmrTelemetryInjection, AmrClient
  - Marker interfaces (nominal capability flags):
      AmrCapability, SupportsSimulation, SupportsDocking, SupportsManualDrive,
      SupportsRelocation, SupportsMapNodes, SupportsTelemetryInjection
  - Enums: Capability, ConnectionState
"""

from .contract import (
    AmrClient,
    AmrConnection,
    AmrMotion,
    AmrStateReader,
    AmrTelemetryInjection,
)
from .enums import Capability, ConnectionState
from .markers import (
    AmrCapability,
    SupportsDocking,
    SupportsManualDrive,
    SupportsMapNodes,
    SupportsRelocation,
    SupportsSimulation,
    SupportsTelemetryInjection,
)

__all__ = [
    # Protocols / 구조적 인터페이스
    "AmrConnection",
    "AmrMotion",
    "AmrStateReader",
    "AmrTelemetryInjection",
    "AmrClient",
    # Marker interfaces / 명목적 능력 marker
    "AmrCapability",
    "SupportsSimulation",
    "SupportsDocking",
    "SupportsManualDrive",
    "SupportsRelocation",
    "SupportsMapNodes",
    "SupportsTelemetryInjection",
    # Enums
    "Capability",
    "ConnectionState",
]
