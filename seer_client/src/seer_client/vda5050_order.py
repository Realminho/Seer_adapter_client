"""Build FMS-style VDA5050 order payloads for SEER test/operator tools.

The production Adapter already consumes VDA5050 ``order`` messages.  This
module lets the SEER-only WebUI and manual FMS console create the same
``nodes[]``/``edges[]`` structure without changing anything under ``adaptor/``.
Map cache coordinates are native SEER metres/radians and are converted to the
configured VDA factsheet units before being placed in ``nodePosition``.
"""

from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .map_view import read_map_cache




def build_vda_action(
    action_type: str,
    parameters: Optional[Mapping[str, Any]] = None,
    *,
    action_id: Optional[str] = None,
    blocking_type: str = "NONE",
) -> Dict[str, Any]:
    """Build one VDA5050 action object usable in node/edge/instant action arrays."""

    return {
        "actionId": str(action_id or f"seer-{uuid.uuid4().hex[:12]}"),
        "actionType": str(action_type),
        "blockingType": str(blocking_type or "NONE").upper(),
        "actionParameters": [
            {"key": str(key), "value": value}
            for key, value in (parameters or {}).items()
        ],
    }

def _position_from_native(value: float, unit: str) -> float:
    token = str(unit or "m").strip().lower()
    scale = {"m": 1.0, "cm": 100.0, "mm": 1000.0}.get(token)
    if scale is None:
        raise ValueError(f"unsupported VDA position unit: {unit!r}")
    return float(value) * scale


def _angle_from_native(value: float, unit: str) -> float:
    token = str(unit or "rad").strip().lower()
    if token == "rad":
        return float(value)
    if token == "deg":
        return math.degrees(float(value))
    raise ValueError(f"unsupported VDA orientation unit: {unit!r}")


def native_pose_to_node_position(
    x: float,
    y: float,
    theta: float,
    *,
    map_id: str,
    position_unit: str,
    orientation_unit: str,
    allowed_deviation_xy: Optional[float] = 2.0,
    allowed_deviation_theta: Optional[float] = 5.0,
) -> Dict[str, Any]:
    """Convert native SEER m/rad pose into one VDA ``nodePosition`` object."""

    result: Dict[str, Any] = {
        "x": _position_from_native(float(x), position_unit),
        "y": _position_from_native(float(y), position_unit),
        "mapId": str(map_id or ""),
        "theta": _angle_from_native(float(theta), orientation_unit),
    }
    if allowed_deviation_xy is not None:
        result["allowedDeviationXY"] = float(allowed_deviation_xy)
    if allowed_deviation_theta is not None:
        result["allowedDeviationTheta"] = float(allowed_deviation_theta)
    return result


def route_metadata_from_map(
    map_cache_path: Optional[Path],
    route_points: Sequence[str],
    *,
    position_unit: str,
    orientation_unit: str,
    fallback_map_id: str = "",
    allowed_deviation_xy: Optional[float] = 2.0,
    allowed_deviation_theta: Optional[float] = 5.0,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[Tuple[str, str], str], str]:
    """Return node positions, directed edge ids, and map id for a route.

    Missing map points are intentionally left without ``nodePosition`` rather
    than fabricated.  The unchanged Adapter can still resolve named nodes from
    the live robot map; when the map cache is present, the emitted JSON matches
    what an FMS normally sends and the simulator receives explicit positions.
    """

    model = read_map_cache(Path(map_cache_path)) if map_cache_path else None
    map_id = str(fallback_map_id or "")
    positions: Dict[str, Dict[str, Any]] = {}
    edge_ids: Dict[Tuple[str, str], str] = {}
    wanted = {str(point).strip() for point in route_points if str(point).strip()}

    if not isinstance(model, Mapping):
        return positions, edge_ids, map_id

    map_id = str(model.get("map_name") or map_id)
    for item in model.get("advanced_points", ()) or ():
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "") or "").strip()
        if name not in wanted:
            continue
        try:
            x = float(item["x"])
            y = float(item["y"])
            theta = float(item.get("dir") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        positions[name] = native_pose_to_node_position(
            x,
            y,
            theta,
            map_id=map_id,
            position_unit=position_unit,
            orientation_unit=orientation_unit,
            allowed_deviation_xy=allowed_deviation_xy,
            allowed_deviation_theta=allowed_deviation_theta,
        )

    for item in model.get("curves", ()) or ():
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("start_name", "") or "").strip()
        target = str(item.get("end_name", "") or "").strip()
        if not source or not target:
            continue
        name = str(item.get("name", "") or "").strip()
        if name:
            edge_ids.setdefault((source, target), name)

    return positions, edge_ids, map_id


def build_route_order(
    factory: Any,
    *,
    order_id: str,
    route_points: Sequence[str],
    map_cache_path: Optional[Path] = None,
    position_unit: str = "mm",
    orientation_unit: str = "deg",
    map_id: str = "",
    allowed_deviation_xy: Optional[float] = 2.0,
    allowed_deviation_theta: Optional[float] = 5.0,
    explicit_positions: Optional[Mapping[str, Mapping[str, Any]]] = None,
    node_actions: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    edge_actions: Optional[Mapping[Tuple[str, str], Sequence[Mapping[str, Any]]]] = None,
    order_update_id: int = 0,
) -> Dict[str, Any]:
    """Create one released VDA order using the same schema as an external FMS."""

    points = tuple(str(point).strip() for point in route_points if str(point).strip())
    if not points:
        raise ValueError("VDA order requires at least one node")
    if any(points[index] == points[index - 1] for index in range(1, len(points))):
        raise ValueError("VDA order route contains duplicate adjacent nodes")

    positions, edge_ids, _resolved_map = route_metadata_from_map(
        map_cache_path,
        points,
        position_unit=position_unit,
        orientation_unit=orientation_unit,
        fallback_map_id=map_id,
        allowed_deviation_xy=allowed_deviation_xy,
        allowed_deviation_theta=allowed_deviation_theta,
    )
    if explicit_positions:
        for key, value in explicit_positions.items():
            if isinstance(value, Mapping):
                positions[str(key)] = dict(value)

    return factory.order(
        order_id,
        points,
        node_positions=positions,
        edge_ids=edge_ids,
        node_actions=node_actions,
        edge_actions=edge_actions,
        order_update_id=order_update_id,
    )
