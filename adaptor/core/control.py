"""Build VDA5050 ``instantActions`` payloads for the Control view.

:func:`build_instant_actions` is pure and produces a dict that satisfies
``protocol.vda5050_3_0.messages.InstantActions.from_dict`` (which requires a
full header plus ``actions[]`` where each action has ``actionType``,
``actionId`` and ``blockingType``). It is published to ``{prefix}/instantActions``
by :meth:`MqttMonitor.publish_json`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def build_instant_actions(
    *,
    header_id: int,
    timestamp: str,
    version: str,
    manufacturer: str,
    serial_number: str,
    action_type: str,
    action_id: str,
    blocking_type: str = "NONE",
    parameters: Optional[List[Tuple[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return an InstantActions message dict for a single action."""
    action_parameters = [
        {"key": key, "value": value} for key, value in (parameters or [])
    ]
    return {
        "headerId": header_id,
        "timestamp": timestamp,
        "version": version,
        "manufacturer": manufacturer,
        "serialNumber": serial_number,
        "actions": [
            {
                "actionId": action_id,
                "actionType": action_type,
                "blockingType": blocking_type,
                "actionParameters": action_parameters,
            }
        ],
    }
