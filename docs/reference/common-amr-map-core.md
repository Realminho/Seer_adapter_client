# Common AMR Map Core

Date: 2026-06-12

## Purpose

Common AMR Map Core is the exchange contract produced by
`unified-amr-adaptor` after reading vendor-native map data from JIBOT, SEER,
ROS, or future AMR systems.

The core map is not tied to machine-wise or WCS database tables. Machine-wise
receives this payload and converts it into its own `Space`, `Node`, `Edge`,
`SpaceToNode`, `SpaceToEdge`, and `SpaceElement` models.

## Direction

Use a vendor-neutral AMR map model as the system boundary.

```text
JIBOT / SEER / ROS native map
        |
        v
unified-amr-adaptor importer
        |
        v
Common AMR Map Core payload
        |
        +--> machine-wise / wcs-nodejs converter
        +--> Space 2D Map editor
        +--> vendor exporter
        |
        v
JIBOT / SEER / ROS native map
```

This keeps the common rule focused on AMR map meaning instead of one consumer's
storage schema.

## Top-Level Payload

```json
{
  "schemaVersion": "uamap.core.v1",
  "map": {},
  "coordinateSystem": {},
  "graph": {
    "nodes": [],
    "edges": []
  },
  "zones": [],
  "layers": {
    "occupancy": null,
    "featureLines": [],
    "backgroundImages": []
  },
  "nativeSources": [],
  "changes": [],
  "warnings": [],
  "vendorExtensions": {}
}
```

## Coordinate Rules

The canonical coordinate unit is meters and the canonical heading unit is
radians. Importers must preserve source units in each entity when conversion is
lossy or important for export.

```json
{
  "canonicalUnit": "m",
  "thetaUnit": "rad",
  "sourceUnit": "mm",
  "sourceThetaUnit": "rad",
  "resolution": 0.02,
  "origin": { "x": 0, "y": 0, "theta": 0 },
  "bounds": { "minX": 0, "minY": -10.439, "maxX": 38.26, "maxY": 12.968 },
  "frameId": "map"
}
```

## Graph Nodes

Graph nodes are routable or addressable AMR points. Examples:

- JIBOT `Goal`, `GoalWithHeading`, `Dock`, `PathPoint`
- SEER `advancedPointList` items such as `LocationMark`
- ROS waypoints or named poses

```json
{
  "id": "F1_40",
  "kind": "goal",
  "name": "F1_40",
  "pose": { "x": 5.953, "y": 4.854, "theta": 0, "mapId": "lab2m" },
  "enabled": true,
  "sourceRefs": [
    { "sourceId": "jibot:lab2m", "path": "Objs.Goal[F1_40]" }
  ],
  "sourcePose": { "x": 5953, "y": 4854, "theta": 0, "unit": "mm" },
  "vendorExtensions": {
    "jibot": { "allowPassingThrough": false }
  }
}
```

Allowed `kind` values for the first version:

- `goal`
- `dock`
- `charger`
- `station`
- `waypoint`
- `pathPoint`

## Graph Edges

Graph edges define AMR route connectivity. They may reference straight lines,
polylines, or Bezier curves.

```json
{
  "id": "LM3-LM4",
  "fromNodeId": "LM3",
  "toNodeId": "LM4",
  "direction": "UNIDIRECTIONAL",
  "enabled": true,
  "cost": 1,
  "geometry": {
    "type": "bezier",
    "controlPoints": [
      { "x": 3.382, "y": -1.754 },
      { "x": 4.492, "y": -1.76 }
    ]
  },
  "constraints": {},
  "sourceRefs": [
    { "sourceId": "seer:sam_sdc_test_1", "path": "advancedCurveList[LM3-LM4]" }
  ],
  "vendorExtensions": {
    "seer": { "movestyle": 0, "direction": 0 }
  }
}
```

Allowed `direction` values:

- `UNIDIRECTIONAL`
- `BIDIRECTIONAL`

If a vendor has forward/reverse semantics beyond this, preserve the detail in
`vendorExtensions` and let each exporter decide how to encode it.

## Zones

Zones describe areas with operational constraints.

```json
{
  "id": "F5#S3#B3",
  "kind": "avoid",
  "name": "F5#S3#B3",
  "geometry": {
    "type": "polygon",
    "points": [
      { "x": 6.975, "y": 6.493 },
      { "x": 8.276, "y": 6.493 },
      { "x": 8.276, "y": 8.119 },
      { "x": 6.975, "y": 8.119 }
    ]
  },
  "enabled": true,
  "sourceRefs": [
    { "sourceId": "jibot:lab2m", "path": "Objs.AvoidArea[F5#S3#B3]" }
  ],
  "vendorExtensions": {}
}
```

Allowed `kind` values for the first version:

- `avoid`
- `restricted`
- `speedLimit`
- `safety`
- `work`
- `parking`
- `queue`

## Layers

Layers hold display or localization geometry that is not necessarily routable.

### Occupancy

Occupancy may be stored as source-native references for large maps or inline
points for small maps.

```json
{
  "type": "pointCloud",
  "pointCount": 15810,
  "encoding": "nativeRef",
  "sourceRef": { "sourceId": "jibot:lab2m", "path": "ObsPoints" }
}
```

### Feature Lines

SEER `advancedLineList` and similar wall/feature data maps here.

```json
{
  "id": "feature-line-1",
  "kind": "featureLine",
  "start": { "x": 5.41, "y": 0.02 },
  "end": { "x": 5.3, "y": 0.114 },
  "sourceRefs": [
    { "sourceId": "seer:sam_sdc_test_1", "path": "advancedLineList[0]" }
  ],
  "vendorExtensions": {
    "seer": { "direction": 1 }
  }
}
```

## Native Sources

Native source records preserve round-trip and export context without forcing
all vendor fields into the common schema.

```json
{
  "sourceId": "seer:sam_sdc_test_1",
  "vendor": "seer",
  "sourceType": "smap",
  "mapId": "sam_sdc_test_1",
  "hash": "sha256:...",
  "rawRef": "seer-client/sam_sdc_test_1.smap"
}
```

Importers may include `rawPayload` for small payloads or tests, but production
flows should prefer `rawRef` plus `hash` to avoid duplicating large map data.

## Warnings

Importers record non-fatal issues in `warnings` instead of dropping data
silently. Each warning is `{ "code", "ref", "reason" }`, where `ref` points at
the source path that triggered it.

```json
{
  "code": "unsupportedSeerPointClass",
  "ref": "advancedPointList[CP1]",
  "reason": "SEER advancedPointList class 'ChargePoint' is not mapped to the common schema"
}
```

First-version warning codes:

- `duplicateNodeId` — two source objects resolve to the same node id; the first
  is kept and the rest are skipped.
- `duplicateEdgeId` — two connections resolve to the same edge id; the first is
  kept and the rest are skipped.
- `unsupportedJibotCategory` — a JIBOT `Objs` category is not mapped yet.
- `unsupportedSeerPointClass` — a SEER `advancedPointList` entry is not a
  `LocationMark` and is not mapped yet.

A clean map produces an empty `warnings` array. A non-empty array is a signal
that the source held data the current importer does not yet represent.

## Machine-Wise Conversion Rule

Machine-wise receives Common AMR Map Core and converts it into its own storage
model.

```text
map metadata      -> Space.extraData.amrMap
graph.nodes       -> Node + SpaceToNode
graph.edges       -> Edge + SpaceToEdge
zones             -> SpaceElement(type=ZONE)
featureLines      -> SpaceElement(type=WALL)
occupancy/images  -> SpaceElement(type=IMAGE) or Space.extraData.amrMap layer refs
sourceRefs        -> entity extraData.amrMap.sourceRefs
vendorExtensions  -> entity extraData.amrMap.vendorExtensions
```

The common schema is the inter-system contract. WCS node/edge tables are a
consumer-side projection, not the source standard.

## First-Version Vendor Mapping

### JIBOT

- `raw.Header == "umcl-map"` identifies the source.
- `MapRes`, `MinPose`, `MaxPose` define bounds and resolution.
- `Objs.Goal` -> graph node `kind=goal`
- `Objs.GoalWithHeading` -> graph node `kind=goal`
- `Objs.Dock` -> graph node `kind=dock`
- `Objs.PathPoint` -> graph node `kind=pathPoint`
- `PathPoint.vertex` -> graph edges
- `Objs.AvoidArea` -> zones `kind=avoid`. A two-point AvoidArea is a min/max
  bounding box and is expanded into a closed, axis-aligned four-vertex polygon;
  three or more points are kept as an explicit polygon.
- `ObsPoints` -> occupancy layer by native reference
- Unmapped `Objs` categories -> `unsupportedJibotCategory` warning

JIBOT source coordinates are treated as millimeters and converted to meters in
canonical `pose`.

### SEER

- `header.mapName` is the default map id.
- `header.resolution`, `minPos`, `maxPos` define coordinate metadata.
- `advancedPointList` `LocationMark` -> graph node `kind=waypoint`
- Non-`LocationMark` `advancedPointList` entries -> `unsupportedSeerPointClass` warning
- `advancedCurveList` -> graph edge with Bezier geometry
- `advancedLineList` -> feature line layer
- `normalPosList` -> occupancy layer by native reference

SEER source coordinates are treated as meters.

## Export Rule

Exporters must use the common entity plus `sourceRefs` and vendor extensions.
If an edited entity cannot be represented in a target vendor format, the
exporter must return a warning or failure item instead of silently dropping it.

```json
{
  "status": "warning",
  "entityId": "zone-1",
  "targetVendor": "jibot",
  "reason": "speedLimit zones are not supported by the current JIBOT exporter"
}
```
