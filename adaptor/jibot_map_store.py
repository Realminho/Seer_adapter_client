"""Persist the real JIBOT map so the simulator can build itself from it.

The real robot reports its map (UmGetMap) at runtime. We snapshot the parsed
node positions plus the raw response to disk; on a ``--simulator`` run the
adapter loads that snapshot so the simulator drives on the real map layout
instead of an empty world.
"""

import datetime
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _as_node_tuple(value: Any) -> Optional[Tuple[float, float, float]]:
    """Coerce a stored ``[x, y, theta]`` (or ``[x, y]``) into a float tuple."""
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        x = float(value[0])
        y = float(value[1])
        theta = float(value[2]) if len(value) > 2 else 0.0
    except (TypeError, ValueError):
        return None
    return (x, y, theta)


def save_map_snapshot(
    path: Path,
    *,
    map_id: str,
    nodes: Dict[str, Any],
    raw: Any,
    serial_number: str,
) -> bool:
    """Write a map snapshot atomically.

    Returns False when there are no usable nodes to persist, so a transient
    empty/failed UmGetMap never overwrites a good saved map with nothing.
    """
    clean_nodes: Dict[str, Tuple[float, float, float]] = {}
    for name, value in (nodes or {}).items():
        node = _as_node_tuple(value)
        if node is not None:
            clean_nodes[str(name)] = node
    if not clean_nodes:
        return False

    payload = {
        "serialNumber": serial_number,
        "mapId": map_id,
        "nodes": {name: list(node) for name, node in clean_nodes.items()},
        "raw": raw,
        "updatedAt": datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return True


def load_map_snapshot(path: Path) -> Optional[Dict[str, Any]]:
    """Load a map snapshot as ``{"map_id", "nodes", "raw"}``, or None.

    ``nodes`` is rebuilt as ``{name: (x, y, theta)}`` tuples. Returns None when
    the file is missing, unparseable, or carries no usable nodes.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None

    nodes: Dict[str, Tuple[float, float, float]] = {}
    for name, value in (payload.get("nodes") or {}).items():
        node = _as_node_tuple(value)
        if node is not None:
            nodes[str(name)] = node
    if not nodes:
        return None

    return {
        "map_id": payload.get("mapId", ""),
        "nodes": nodes,
        "raw": payload.get("raw"),
    }
