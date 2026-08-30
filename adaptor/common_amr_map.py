"""Common AMR Map Core import helpers.

The functions in this module convert vendor-native map payloads into the
``uamap.core.v1`` exchange shape used between the adaptor and downstream
systems such as machine-wise.
"""

import hashlib
import json
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple


SCHEMA_VERSION = "uamap.core.v1"


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode(
        "utf-8"
    )
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _source_id(vendor: str, map_id: str) -> str:
    return f"{vendor}:{map_id or 'unknown'}"


def _source_ref(source_id: str, path: str) -> Dict[str, str]:
    return {"sourceId": source_id, "path": path}


def _parse_pose(value: Any) -> Optional[Tuple[float, float, float]]:
    if isinstance(value, str):
        parts = value.split()
    elif isinstance(value, (list, tuple)):
        parts = value
    else:
        return None
    if len(parts) < 2:
        return None
    try:
        x = float(parts[0])
        y = float(parts[1])
        theta = float(parts[2]) if len(parts) > 2 else 0.0
    except (TypeError, ValueError):
        return None
    return x, y, theta


def _parse_jibot_bounds(raw: Dict[str, Any]) -> Dict[str, float]:
    min_pose = _parse_pose(raw.get("MinPose")) or (0.0, 0.0, 0.0)
    max_pose = _parse_pose(raw.get("MaxPose")) or (0.0, 0.0, 0.0)
    return {
        "minX": _mm_to_m(min_pose[0]),
        "minY": _mm_to_m(min_pose[1]),
        "maxX": _mm_to_m(max_pose[0]),
        "maxY": _mm_to_m(max_pose[1]),
    }


def _mm_to_m(value: float) -> float:
    return float(value) / 1000.0


def _jibot_pose(raw_pose: Tuple[float, float, float], map_id: str) -> Dict[str, Any]:
    x, y, theta = raw_pose
    return {"x": _mm_to_m(x), "y": _mm_to_m(y), "theta": math.radians(float(theta)), "mapId": map_id}


def _source_pose(raw_pose: Tuple[float, float, float], unit: str) -> Dict[str, Any]:
    x, y, theta = raw_pose
    return {"x": x, "y": y, "theta": theta, "unit": unit}


def _property_map(properties: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for prop in properties or []:
        if not isinstance(prop, dict):
            continue
        key = prop.get("key")
        if not key:
            continue
        for value_key in ("boolValue", "int32Value", "doubleValue", "stringValue", "value"):
            if value_key in prop:
                out[str(key)] = prop[value_key]
                break
    return out


def _count_jibot_obs_points(obs_points: Any) -> int:
    if not isinstance(obs_points, list):
        return 0
    count = 0
    for row in obs_points:
        if isinstance(row, list) and len(row) > 1:
            count += len(row) - 1
    return count


def _decode_jibot_obs_points(obs_points: Any, max_points: int = 4000) -> list:
    """JIBOT ObsPoints(라이다 점군)를 canonical(m) {x,y} 목록으로 디코딩한다.

    인코딩 가정: 각 row = [x_mm, y1_mm, y2_mm, ...] — 첫 값이 공유 x(mm), 나머지가 y(mm).
    (_count_jibot_obs_points가 len-1을 점 수로 세는 것과 일관) → /1000 으로 m 변환.
    max_points 초과 시 균등 다운샘플(payload 크기 제한). JIBOT 인코딩 가정이라 표출 후 보정 가능.
    """
    if not isinstance(obs_points, list):
        return []
    pts: list = []
    for row in obs_points:
        if not isinstance(row, list) or len(row) < 2:
            continue
        try:
            x = float(row[0]) / 1000.0
        except (TypeError, ValueError):
            continue
        for y in row[1:]:
            try:
                pts.append({"x": x, "y": float(y) / 1000.0})
            except (TypeError, ValueError):
                continue
    if max_points and len(pts) > max_points:
        step = len(pts) // max_points + 1
        pts = pts[::step]
    return pts


def _blank_map(
    *,
    map_id: str,
    name: str,
    version: str,
    source_vendor: str,
    coordinate_system: Dict[str, Any],
    native_source: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "map": {
            "mapId": map_id,
            "name": name,
            "version": version,
            "sourceVendor": source_vendor,
        },
        "coordinateSystem": coordinate_system,
        "graph": {"nodes": [], "edges": []},
        "zones": [],
        "layers": {"occupancy": None, "featureLines": [], "backgroundImages": []},
        "nativeSources": [native_source],
        "changes": [],
        "warnings": [],
        "vendorExtensions": {},
    }


def _add_warning(common: Dict[str, Any], code: str, ref: str, reason: str) -> None:
    common["warnings"].append({"code": code, "ref": ref, "reason": reason})


def _append_node(
    common: Dict[str, Any], seen_ids: set, node: Dict[str, Any], ref: str
) -> None:
    """Append a graph node, guarding against duplicate ids.

    Two source objects can share a name; emitting both would produce an
    invalid graph with duplicate node ids. Keep the first occurrence and
    record a warning instead of silently overwriting downstream.
    """
    node_id = node["id"]
    if node_id in seen_ids:
        _add_warning(
            common,
            "duplicateNodeId",
            ref,
            f"node id {node_id!r} already exists; keeping the first occurrence",
        )
        return
    seen_ids.add(node_id)
    common["graph"]["nodes"].append(node)


def _append_edge(
    common: Dict[str, Any], seen_ids: set, edge: Dict[str, Any], ref: str
) -> None:
    """Append a graph edge, guarding against duplicate ids.

    Keep the first occurrence and record a warning so a duplicate connection
    (for example a vertex list naming the same target twice) does not silently
    produce two edges sharing an id.
    """
    edge_id = edge["id"]
    if edge_id in seen_ids:
        _add_warning(
            common,
            "duplicateEdgeId",
            ref,
            f"edge id {edge_id!r} already exists; keeping the first occurrence",
        )
        return
    seen_ids.add(edge_id)
    common["graph"]["edges"].append(edge)


def from_jibot_snapshot(snapshot: Dict[str, Any], raw_ref: Optional[str] = None) -> Dict[str, Any]:
    """Convert a JIBOT saved map snapshot or raw UmGetMap payload to Common AMR Map Core."""
    raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else snapshot
    map_id = str(snapshot.get("mapId") or snapshot.get("map_id") or raw.get("MapName") or "jibot-map")
    source_id = _source_id("jibot", map_id)
    map_res = float(raw.get("MapRes") or 0.0)

    common = _blank_map(
        map_id=map_id,
        name=map_id,
        version=str(raw.get("Header") or "umcl-map"),
        source_vendor="jibot",
        coordinate_system={
            "canonicalUnit": "m",
            "thetaUnit": "rad",
            "sourceUnit": "mm",
            "sourceThetaUnit": "deg",
            "resolution": _mm_to_m(map_res) if map_res else None,
            "origin": {"x": 0, "y": 0, "theta": 0},
            "bounds": _parse_jibot_bounds(raw),
            "frameId": "map",
        },
        native_source={
            "sourceId": source_id,
            "vendor": "jibot",
            "sourceType": "um-get-map",
            "mapId": map_id,
            "hash": _stable_hash(raw),
            "rawRef": raw_ref,
        },
    )

    seen_ids: set = set()
    objs = raw.get("Objs") if isinstance(raw.get("Objs"), dict) else {}
    # Default JIBOT category for each kind (used to skip tagging the default).
    _kind_default_category = {"goal": "Goal", "dock": "Dock"}

    for category, kind in (
        ("Goal", "goal"),
        ("GoalWithHeading", "goal"),
        ("Dock", "dock"),
    ):
        for item in objs.get(category, []) or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            raw_pose = _parse_pose(item.get("pose"))
            if not name or raw_pose is None:
                continue
            ref = f"Objs.{category}[{name}]"
            jibot_ve: Dict[str, Any] = {
                key: value
                for key, value in item.items()
                if key not in {"name", "pose"}
            }
            # Preserve the source JIBOT category when it differs from the
            # default for that kind (e.g. GoalWithHeading vs. Goal).
            if category != _kind_default_category.get(kind):
                jibot_ve["objCategory"] = category
            _append_node(
                common,
                seen_ids,
                {
                    "id": str(name),
                    "kind": kind,
                    "name": str(name),
                    "pose": _jibot_pose(raw_pose, map_id),
                    "enabled": True,
                    "sourceRefs": [_source_ref(source_id, ref)],
                    "sourcePose": _source_pose(raw_pose, "mm"),
                    "vendorExtensions": {
                        "jibot": jibot_ve,
                    },
                },
                ref,
            )

    path_points: Dict[str, Tuple[float, float, float]] = {}
    for item in objs.get("PathPoint", []) or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        raw_pose = _parse_pose(item.get("pose"))
        if not name or raw_pose is None:
            continue
        name = str(name)
        path_points[name] = raw_pose
        ref = f"Objs.PathPoint[{name}]"
        _append_node(
            common,
            seen_ids,
            {
                "id": name,
                "kind": "pathPoint",
                "name": name,
                "pose": _jibot_pose(raw_pose, map_id),
                "enabled": True,
                "sourceRefs": [_source_ref(source_id, ref)],
                "sourcePose": _source_pose(raw_pose, "mm"),
                "vendorExtensions": {
                    "jibot": {
                        key: value
                        for key, value in item.items()
                        if key not in {"name", "pose", "vertex", "costs"}
                    }
                },
            },
            ref,
        )

    seen_edge_ids: set = set()
    for item in objs.get("PathPoint", []) or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        from_id = str(item["name"])
        targets = str(item.get("vertex") or "").split()
        costs = item.get("costs") if isinstance(item.get("costs"), list) else []
        for index, to_id in enumerate(targets):
            cost = costs[index] if index < len(costs) else 1
            geometry: Dict[str, Any] = {"type": "line"}
            if from_id in path_points and to_id in path_points:
                geometry["points"] = [
                    _jibot_pose(path_points[from_id], map_id),
                    _jibot_pose(path_points[to_id], map_id),
                ]
            edge_ref = f"Objs.PathPoint[{from_id}].vertex[{index}]"
            _append_edge(
                common,
                seen_edge_ids,
                {
                    "id": f"{from_id}->{to_id}",
                    "fromNodeId": from_id,
                    "toNodeId": str(to_id),
                    "direction": "UNIDIRECTIONAL",
                    "enabled": True,
                    "cost": cost,
                    "geometry": geometry,
                    "constraints": {},
                    "sourceRefs": [_source_ref(source_id, edge_ref)],
                    "vendorExtensions": {"jibot": {}},
                },
                edge_ref,
            )

    for item in objs.get("AvoidArea", []) or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        raw_points = _parse_jibot_points(item.get("points"))
        if not name or not raw_points:
            continue
        common["zones"].append(
            {
                "id": str(name),
                "kind": "avoid",
                "name": str(name),
                "geometry": {
                    "type": "polygon",
                    "points": _jibot_zone_polygon(raw_points),
                },
                "enabled": True,
                "sourceRefs": [_source_ref(source_id, f"Objs.AvoidArea[{name}]")],
                "vendorExtensions": {
                    "jibot": {
                        key: value
                        for key, value in item.items()
                        if key not in {"name", "points"}
                    }
                },
            }
        )

    handled_categories = {"Goal", "GoalWithHeading", "Dock", "PathPoint", "AvoidArea"}
    for category in objs:
        if category not in handled_categories:
            _add_warning(
                common,
                "unsupportedJibotCategory",
                f"Objs.{category}",
                f"JIBOT Objs category {category!r} is not mapped to the common schema",
            )

    obs_count = _count_jibot_obs_points(raw.get("ObsPoints"))
    if obs_count:
        sampled = _decode_jibot_obs_points(raw.get("ObsPoints"))
        common["layers"]["occupancy"] = {
            "type": "pointCloud",
            "pointCount": obs_count,
            "encoding": "inline",
            "points": sampled,
            "sampledCount": len(sampled),
            "sourceRef": _source_ref(source_id, "ObsPoints"),
        }

    return common


def _jibot_zone_polygon(raw_points: List[Tuple[float, float]]) -> List[Dict[str, float]]:
    """Return a meter-unit polygon ring for a JIBOT AvoidArea.

    A JIBOT AvoidArea given as exactly two points is a min/max bounding box,
    not a 2-vertex polygon. Expand it into a closed, axis-aligned rectangle so
    downstream consumers render an area instead of a degenerate line. Point
    lists with three or more vertices are treated as explicit polygons.
    """
    if len(raw_points) == 2:
        (x0, y0), (x1, y1) = raw_points
        min_x, max_x = sorted((x0, x1))
        min_y, max_y = sorted((y0, y1))
        raw_points = [
            (min_x, min_y),
            (max_x, min_y),
            (max_x, max_y),
            (min_x, max_y),
        ]
    return [{"x": _mm_to_m(x), "y": _mm_to_m(y)} for x, y in raw_points]


def _parse_jibot_points(value: Any) -> List[Tuple[float, float]]:
    if isinstance(value, str):
        parts = value.split()
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        return []
    if len(parts) < 4 or len(parts) % 2 != 0:
        return []
    points: List[Tuple[float, float]] = []
    for i in range(0, len(parts), 2):
        try:
            points.append((float(parts[i]), float(parts[i + 1])))
        except (TypeError, ValueError):
            return []
    return points


def from_seer_smap(payload: Dict[str, Any], raw_ref: Optional[str] = None) -> Dict[str, Any]:
    """Convert a SEER ``.smap`` JSON payload to Common AMR Map Core."""
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    map_id = str(header.get("mapName") or "seer-map")
    source_id = _source_id("seer", map_id)
    min_pos = header.get("minPos") if isinstance(header.get("minPos"), dict) else {}
    max_pos = header.get("maxPos") if isinstance(header.get("maxPos"), dict) else {}

    common = _blank_map(
        map_id=map_id,
        name=map_id,
        version=str(header.get("version") or ""),
        source_vendor="seer",
        coordinate_system={
            "canonicalUnit": "m",
            "thetaUnit": "rad",
            "sourceUnit": "m",
            "sourceThetaUnit": "rad",
            "resolution": header.get("resolution"),
            "origin": {"x": 0, "y": 0, "theta": 0},
            "bounds": {
                "minX": min_pos.get("x"),
                "minY": min_pos.get("y"),
                "maxX": max_pos.get("x"),
                "maxY": max_pos.get("y"),
            },
            "frameId": "map",
        },
        native_source={
            "sourceId": source_id,
            "vendor": "seer",
            "sourceType": "smap",
            "mapId": map_id,
            "hash": _stable_hash(payload),
            "rawRef": raw_ref,
        },
    )

    seen_ids: set = set()
    for item in payload.get("advancedPointList", []) or []:
        if not isinstance(item, dict):
            continue
        class_name = item.get("className")
        if class_name != "LocationMark":
            _add_warning(
                common,
                "unsupportedSeerPointClass",
                f"advancedPointList[{item.get('instanceName') or class_name}]",
                f"SEER advancedPointList class {class_name!r} is not mapped to the common schema",
            )
            continue
        name = item.get("instanceName")
        pos = item.get("pos") if isinstance(item.get("pos"), dict) else {}
        if not name or "x" not in pos or "y" not in pos:
            continue
        theta = float(item.get("dir") or 0.0)
        ref = f"advancedPointList[{name}]"
        _append_node(
            common,
            seen_ids,
            {
                "id": str(name),
                "kind": "waypoint",
                "name": str(name),
                "pose": {
                    "x": float(pos["x"]),
                    "y": float(pos["y"]),
                    "theta": theta,
                    "mapId": map_id,
                },
                "enabled": True,
                "sourceRefs": [_source_ref(source_id, ref)],
                "sourcePose": {
                    "x": float(pos["x"]),
                    "y": float(pos["y"]),
                    "theta": theta,
                    "unit": "m",
                },
                "vendorExtensions": {"seer": _property_map(item.get("property", []))},
            },
            ref,
        )

    seen_edge_ids: set = set()
    for index, item in enumerate(payload.get("advancedCurveList", []) or []):
        if not isinstance(item, dict):
            continue
        edge_id = str(item.get("instanceName") or f"curve-{index}")
        start = item.get("startPos") if isinstance(item.get("startPos"), dict) else {}
        end = item.get("endPos") if isinstance(item.get("endPos"), dict) else {}
        from_id = start.get("instanceName")
        to_id = end.get("instanceName")
        if (not from_id or not to_id) and "-" in edge_id:
            from_id, to_id = edge_id.split("-", 1)
        if not from_id or not to_id:
            continue
        control_points = []
        for key in ("controlPos1", "controlPos2"):
            point = item.get(key) if isinstance(item.get(key), dict) else None
            if point and "x" in point and "y" in point:
                control_points.append({"x": float(point["x"]), "y": float(point["y"])})
        edge_ref = f"advancedCurveList[{edge_id}]"
        _append_edge(
            common,
            seen_edge_ids,
            {
                "id": edge_id,
                "fromNodeId": str(from_id),
                "toNodeId": str(to_id),
                "direction": "UNIDIRECTIONAL",
                "enabled": True,
                "cost": 1,
                "geometry": {"type": "bezier", "controlPoints": control_points},
                "constraints": {},
                "sourceRefs": [_source_ref(source_id, edge_ref)],
                "vendorExtensions": {"seer": _property_map(item.get("property", []))},
            },
            edge_ref,
        )

    for index, item in enumerate(payload.get("advancedLineList", []) or []):
        if not isinstance(item, dict):
            continue
        line = item.get("line") if isinstance(item.get("line"), dict) else {}
        start = line.get("startPos") if isinstance(line.get("startPos"), dict) else {}
        end = line.get("endPos") if isinstance(line.get("endPos"), dict) else {}
        if not {"x", "y"}.issubset(start) or not {"x", "y"}.issubset(end):
            continue
        line_id = str(item.get("instanceName") or f"feature-line-{index}")
        common["layers"]["featureLines"].append(
            {
                "id": line_id,
                "kind": "featureLine",
                "start": {"x": float(start["x"]), "y": float(start["y"])},
                "end": {"x": float(end["x"]), "y": float(end["y"])},
                "sourceRefs": [_source_ref(source_id, f"advancedLineList[{index}]")],
                "vendorExtensions": {"seer": _property_map(item.get("property", []))},
            }
        )

    normal_positions = payload.get("normalPosList")
    if isinstance(normal_positions, list) and normal_positions:
        common["layers"]["occupancy"] = {
            "type": "pointCloud",
            "pointCount": len(normal_positions),
            "encoding": "nativeRef",
            "sourceRef": _source_ref(source_id, "normalPosList"),
        }

    return common


# ---------------------------------------------------------------------------
# Inverse helpers: uamap canonical → JIBOT raw (write path / reverse mapper)
# ---------------------------------------------------------------------------


def _m_to_mm(value: float) -> float:
    """미터 → 밀리미터 변환. from_jibot_snapshot의 _mm_to_m 역함수.
    Convert metres to millimetres (inverse of _mm_to_m).
    """
    return float(value) * 1000.0


def _format_pose(x_m: float, y_m: float, theta_rad: float) -> str:
    """canonical 미터/라디안 pose → JIBOT raw 문자열 "x_mm y_mm theta_deg".

    Rounds mm values to the nearest integer to match the original source format
    (JIBOT stores integer mm values). Theta is converted rad→deg and rounded
    to 4 decimal places to avoid floating-point noise in the round-trip.

    Format: ``"<x_mm> <y_mm> <theta_deg>"`` where x/y are integers (mm) and
    theta is a float in degrees.
    """
    x_mm = int(round(_m_to_mm(x_m)))
    y_mm = int(round(_m_to_mm(y_m)))
    theta_deg = round(math.degrees(theta_rad), 4)
    return f"{x_mm} {y_mm} {theta_deg}"


def _format_pose_from_source(node: Dict[str, Any]) -> str:
    """sourcePose(raw mm/deg)가 있으면 그대로 재사용해 round-trip 손실 최소화.

    If the node carries a ``sourcePose`` dict (written by ``from_jibot_snapshot``
    via ``_source_pose``), reuse its raw mm/deg values to achieve lossless
    round-trip.  Falls back to recomputing from the canonical ``pose`` when
    sourcePose is absent or has a non-mm unit.

    sourcePose이 있을 때 round-trip에서 부동소수점 오차 없이 원본값을 복원함.
    """
    sp = node.get("sourcePose")
    if isinstance(sp, dict) and sp.get("unit") == "mm":
        x_mm = int(round(float(sp.get("x", 0))))
        y_mm = int(round(float(sp.get("y", 0))))
        theta_deg = round(float(sp.get("theta", 0)), 4)
        return f"{x_mm} {y_mm} {theta_deg}"
    pose = node.get("pose") or {}
    return _format_pose(
        float(pose.get("x", 0)),
        float(pose.get("y", 0)),
        float(pose.get("theta", 0)),
    )


def _format_points(polygon_points: List[Dict[str, float]]) -> str:
    """uamap polygon 4-corner → JIBOT AvoidArea 2-point bbox 문자열 (mm).

    from_jibot_snapshot은 2-point bbox를 4-corner polygon으로 확장함
    (_jibot_zone_polygon). 역변환 시 4-corner polygon의 min/max에서
    bbox 2점("minx miny maxx maxy" mm)을 복원함.

    Converts a 4-corner polygon (metres) back to the 2-point bounding-box
    string ``"minx miny maxx maxy"`` (mm) expected by JIBOT AvoidArea.
    If the input has exactly 2 points they are used directly.
    """
    if not polygon_points:
        return ""
    xs = [_m_to_mm(p.get("x", 0)) for p in polygon_points]
    ys = [_m_to_mm(p.get("y", 0)) for p in polygon_points]
    min_x = int(round(min(xs)))
    min_y = int(round(min(ys)))
    max_x = int(round(max(xs)))
    max_y = int(round(max(ys)))
    return f"{min_x} {min_y} {max_x} {max_y}"


def to_jibot_snapshot(uamap: Dict[str, Any]) -> Dict[str, Any]:
    """uamap(canonical, m, rad) → JIBOT raw map dict(mm, deg).

    from_jibot_snapshot의 정확한 역함수. 생성된 dict를 from_jibot_snapshot에
    다시 넣으면 원본 uamap과 핵심 필드(node id/kind/pose, edge, zone)가 일치함.

    Exact inverse of from_jibot_snapshot. The returned dict, when passed back
    through from_jibot_snapshot, must produce a uamap whose core fields
    (node ids, kinds, poses, edges, zones) match the original.

    Round-trip fidelity strategy:
    - node pose: sourcePose(raw mm/deg) 있으면 그대로 재사용(lossless).
      없으면 canonical pose에서 m→mm, rad→deg 재계산.
    - zone points: polygon 4-corner → bbox min/max 2-point (mm).
    - bounds: coordinateSystem.bounds m → mm for MinPose/MaxPose.
    - MapRes: coordinateSystem.resolution m→mm.
    - occupancy: nativeSources/layers 정보를 보존하지 않음(ObsPoints 생략).
      → round-trip lossiness: occupancy/ObsPoints는 복원 불가.

    Lossiness note:
    - ObsPoints (occupancy point cloud) is not invertible from the uamap
      layer reference; it is omitted in the output. If needed, the caller
      must source the raw ObsPoints from the original nativeSource payload.
    - vendorExtensions on zones (e.g. AvoidArea pose field) are preserved
      in the output if the zone's vendorExtensions.jibot dict is present.

    Args:
        uamap: Common AMR Map dict (uamap.core.v1).

    Returns:
        JIBOT raw map dict suitable for writing to a robot map file.
    """
    warnings: List[str] = []

    map_info = uamap.get("map") or {}
    map_id = str(map_info.get("mapId") or "jibot-map")
    coord_sys = uamap.get("coordinateSystem") or {}
    bounds = coord_sys.get("bounds") or {}
    resolution_m = coord_sys.get("resolution")

    # MapRes: m→mm
    map_res: Any
    if resolution_m is not None:
        map_res = int(round(_m_to_mm(float(resolution_m))))
    else:
        map_res = 0

    # MinPose / MaxPose: bounds m→mm, "x y" format (no theta for bounds)
    min_x_mm = int(round(_m_to_mm(float(bounds.get("minX") or 0))))
    min_y_mm = int(round(_m_to_mm(float(bounds.get("minY") or 0))))
    max_x_mm = int(round(_m_to_mm(float(bounds.get("maxX") or 0))))
    max_y_mm = int(round(_m_to_mm(float(bounds.get("maxY") or 0))))
    min_pose_str = f"{min_x_mm} {min_y_mm}"
    max_pose_str = f"{max_x_mm} {max_y_mm}"

    # Header from map version; MapName from map_id
    header = str(map_info.get("version") or "umcl-map")

    # --- Nodes → Objs categories ---
    # Supported direct mappings: goal→Goal, dock→Dock, pathPoint→PathPoint
    # Fallback kinds (charger/station/waypoint) → PathPoint + warning
    goals: List[Dict[str, Any]] = []
    goals_with_heading: List[Dict[str, Any]] = []
    docks: List[Dict[str, Any]] = []
    path_points_map: Dict[str, Dict[str, Any]] = {}  # id → raw item dict

    graph = uamap.get("graph") or {}
    nodes = graph.get("nodes") or []

    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        kind = str(node.get("kind") or "")
        pose_str = _format_pose_from_source(node)

        # Start with vendorExtensions.jibot for extra fields
        vendor_jibot = {}
        ve = node.get("vendorExtensions") or {}
        if isinstance(ve.get("jibot"), dict):
            vendor_jibot = dict(ve["jibot"])

        # objCategory is a routing hint — strip it out before emitting raw item
        obj_category = vendor_jibot.pop("objCategory", None)

        raw_item: Dict[str, Any] = {"name": node_id, "pose": pose_str, **vendor_jibot}

        if kind == "goal":
            # Restore to GoalWithHeading bucket when the source category says so
            if obj_category == "GoalWithHeading":
                goals_with_heading.append(raw_item)
            else:
                goals.append(raw_item)
        elif kind == "dock":
            docks.append(raw_item)
        elif kind == "pathPoint":
            # vertex/costs filled in after edges loop
            path_points_map[node_id] = raw_item
        else:
            # charger / station / waypoint / unknown → PathPoint fallback
            warnings.append(
                f"node {node_id!r} kind={kind!r} not directly invertible; "
                "mapping to PathPoint fallback"
            )
            path_points_map[node_id] = raw_item

    # --- Edges → PathPoint vertex/costs ---
    # Group edges by fromNodeId, sort by edge order (preserves costs alignment)
    edges = graph.get("edges") or []
    vertex_map: Dict[str, List[str]] = {}   # fromNodeId → [toNodeId, ...]
    costs_map: Dict[str, List[Any]] = {}    # fromNodeId → [cost, ...]

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        from_id = str(edge.get("fromNodeId") or "")
        to_id = str(edge.get("toNodeId") or "")
        cost = edge.get("cost", 1)
        if not from_id or not to_id:
            continue
        vertex_map.setdefault(from_id, []).append(to_id)
        costs_map.setdefault(from_id, []).append(cost)

    # Apply vertex/costs to PathPoint items
    for node_id, item in path_points_map.items():
        targets = vertex_map.get(node_id, [])
        costs = costs_map.get(node_id, [])
        item["vertex"] = " ".join(targets)
        if costs:
            item["costs"] = costs

    # --- Zones → AvoidArea ---
    avoid_areas: List[Dict[str, Any]] = []
    zones = uamap.get("zones") or []
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        zone_id = str(zone.get("id") or "")
        geometry = zone.get("geometry") or {}
        poly_points = geometry.get("points") or []
        points_str = _format_points(poly_points)

        # Restore vendorExtensions.jibot extra fields (e.g. pose)
        ve_zone = zone.get("vendorExtensions") or {}
        jibot_ve = {}
        if isinstance(ve_zone.get("jibot"), dict):
            jibot_ve = dict(ve_zone["jibot"])

        avoid_item: Dict[str, Any] = {"name": zone_id, "points": points_str, **jibot_ve}
        avoid_areas.append(avoid_item)

    objs_out: Dict[str, Any] = {
        "Goal": goals,
        "Dock": docks,
        "PathPoint": list(path_points_map.values()),
        "AvoidArea": avoid_areas,
    }
    if goals_with_heading:
        objs_out["GoalWithHeading"] = goals_with_heading

    raw_out: Dict[str, Any] = {
        "Header": header,
        "MapName": map_id,
        "MapRes": map_res,
        "MinPose": min_pose_str,
        "MaxPose": max_pose_str,
        "Objs": objs_out,
    }

    # occupancy: not invertible from uamap layer reference alone.
    # ObsPoints is omitted. Caller must provide raw ObsPoints separately
    # if real-robot map file needs obstacle data preserved.
    # (occupancy: uamap에서 복원 불가 — ObsPoints 생략. round-trip lossiness 허용.)

    return raw_out
