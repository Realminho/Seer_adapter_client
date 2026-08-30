"""Tests for Common AMR Map Core importers."""

import math
import sys
import unittest
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from common_amr_map import from_jibot_snapshot, from_seer_smap


def _by_id(items):
    return {item["id"]: item for item in items}


class JibotCommonMapImportTest(unittest.TestCase):
    def test_jibot_snapshot_imports_nodes_edges_zones_and_occupancy(self):
        snapshot = {
            "mapId": "lab2m",
            "raw": {
                "Header": "umcl-map",
                "MapRes": 20,
                "MinPose": "0 -1000",
                "MaxPose": "20000 10000",
                "NumPoints": 3,
                "Objs": {
                    "Goal": [
                        {
                            "name": "F1_40",
                            "pose": "5953 4854 0.00",
                            "allowPassingThrough": False,
                        }
                    ],
                    "Dock": [
                        {
                            "name": "F1_CHARGER",
                            "pose": "7290 7268 1.57",
                            "allowPassingThrough": False,
                        }
                    ],
                    "PathPoint": [
                        {
                            "name": "p1",
                            "pose": "1000 1000 0.00",
                            "vertex": "p2 p3",
                            "costs": [1, 2],
                        },
                        {"name": "p2", "pose": "2000 1000 0.00", "vertex": ""},
                        {"name": "p3", "pose": "1000 2000 0.00", "vertex": ""},
                    ],
                    "AvoidArea": [
                        {
                            "name": "avoid-1",
                            "points": "0 0 1000 0 1000 1000 0 1000",
                            "pose": "0 0 0.00",
                        }
                    ],
                },
                "ObsPoints": [[0, 0, 20], [20, 40]],
            },
        }

        common = from_jibot_snapshot(snapshot, raw_ref="runtime/jibot-map.json")

        self.assertEqual(common["schemaVersion"], "uamap.core.v1")
        self.assertEqual(common["map"]["mapId"], "lab2m")
        self.assertEqual(common["coordinateSystem"]["canonicalUnit"], "m")
        self.assertEqual(common["coordinateSystem"]["sourceUnit"], "mm")
        self.assertEqual(common["coordinateSystem"]["bounds"]["minY"], -1.0)

        nodes = _by_id(common["graph"]["nodes"])
        self.assertEqual(nodes["F1_40"]["kind"], "goal")
        self.assertEqual(nodes["F1_40"]["pose"]["x"], 5.953)
        self.assertEqual(nodes["F1_40"]["sourcePose"]["x"], 5953.0)
        self.assertEqual(nodes["F1_CHARGER"]["kind"], "dock")
        self.assertEqual(nodes["p1"]["kind"], "pathPoint")

        edges = _by_id(common["graph"]["edges"])
        self.assertEqual(edges["p1->p2"]["fromNodeId"], "p1")
        self.assertEqual(edges["p1->p2"]["toNodeId"], "p2")
        self.assertEqual(edges["p1->p2"]["cost"], 1)
        self.assertEqual(edges["p1->p3"]["cost"], 2)

        self.assertEqual(common["zones"][0]["kind"], "avoid")
        self.assertEqual(common["zones"][0]["geometry"]["points"][2], {"x": 1.0, "y": 1.0})

        occupancy = common["layers"]["occupancy"]
        self.assertEqual(occupancy["type"], "pointCloud")
        # 라이다 점군을 inline으로 임베드(ObsPoints [x_mm, y1_mm, ...] → m, /1000)
        self.assertEqual(occupancy["encoding"], "inline")
        self.assertEqual(occupancy["pointCount"], 3)
        self.assertEqual(occupancy["sampledCount"], 3)
        self.assertEqual(
            occupancy["points"],
            [{"x": 0.0, "y": 0.0}, {"x": 0.0, "y": 0.02}, {"x": 0.02, "y": 0.04}],
        )
        self.assertEqual(occupancy["sourceRef"]["path"], "ObsPoints")


class JibotAvoidAreaShapeTest(unittest.TestCase):
    def test_two_corner_box_expands_to_closed_rectangle_polygon(self):
        # Mirrors the real runtime map: a JIBOT AvoidArea is a 2-corner
        # min/max box, not a vertex list. Two raw points must not collapse
        # into a degenerate 2-vertex polygon (which renders as a line instead
        # of an area).
        snapshot = {
            "mapId": "lab2m",
            "raw": {
                "Header": "umcl-map",
                "Objs": {
                    "AvoidArea": [
                        {
                            "name": "F5#S3#B3",
                            "points": "6975 6493 8276 8119",
                            "pose": "6975 6493 0.00",
                        }
                    ]
                },
            },
        }

        zone = from_jibot_snapshot(snapshot)["zones"][0]

        self.assertEqual(zone["kind"], "avoid")
        self.assertEqual(zone["geometry"]["type"], "polygon")
        self.assertEqual(
            zone["geometry"]["points"],
            [
                {"x": 6.975, "y": 6.493},
                {"x": 8.276, "y": 6.493},
                {"x": 8.276, "y": 8.119},
                {"x": 6.975, "y": 8.119},
            ],
        )

    def test_explicit_polygon_with_three_or_more_points_is_preserved(self):
        # A real vertex list (>= 3 points) must pass through unchanged.
        snapshot = {
            "mapId": "lab2m",
            "raw": {
                "Header": "umcl-map",
                "Objs": {
                    "AvoidArea": [
                        {
                            "name": "tri",
                            "points": "0 0 2000 0 1000 2000",
                            "pose": "0 0 0.00",
                        }
                    ]
                },
            },
        }

        zone = from_jibot_snapshot(snapshot)["zones"][0]

        self.assertEqual(zone["geometry"]["type"], "polygon")
        self.assertEqual(
            zone["geometry"]["points"],
            [{"x": 0.0, "y": 0.0}, {"x": 2.0, "y": 0.0}, {"x": 1.0, "y": 2.0}],
        )


class SeerCommonMapImportTest(unittest.TestCase):
    def test_seer_smap_imports_landmarks_curves_lines_and_occupancy(self):
        smap = {
            "header": {
                "mapType": "2D-Map",
                "mapName": "sam_sdc_test_1",
                "minPos": {"x": -9.334, "y": -7.825},
                "maxPos": {"x": 33.818, "y": 14.48},
                "resolution": 0.02,
                "version": "1.0.6",
            },
            "normalPosList": [{"x": -9.334, "y": -1.162}, {"x": -9.333, "y": -1.142}],
            "advancedPointList": [
                {
                    "className": "LocationMark",
                    "instanceName": "LM3",
                    "pos": {"x": 2.271, "y": -1.748},
                    "dir": -0.0035,
                    "property": [{"key": "spin", "boolValue": False}],
                },
                {
                    "className": "LocationMark",
                    "instanceName": "LM4",
                    "pos": {"x": 5.602, "y": -1.765},
                    "dir": -0.0102,
                    "property": [{"key": "spin", "boolValue": False}],
                },
            ],
            "advancedLineList": [
                {
                    "className": "FeatureLine",
                    "line": {
                        "startPos": {"x": 5.41, "y": 0.02},
                        "endPos": {"x": 5.3, "y": 0.114},
                    },
                    "property": [{"key": "direction", "doubleValue": 1}],
                }
            ],
            "advancedCurveList": [
                {
                    "className": "DegenerateBezier",
                    "instanceName": "LM3-LM4",
                    "startPos": {"instanceName": "LM3", "pos": {"x": 2.271, "y": -1.748}},
                    "endPos": {"instanceName": "LM4", "pos": {"x": 5.602, "y": -1.765}},
                    "controlPos1": {"x": 3.382, "y": -1.754},
                    "controlPos2": {"x": 4.492, "y": -1.76},
                    "property": [
                        {"key": "direction", "int32Value": 0},
                        {"key": "movestyle", "int32Value": 0},
                    ],
                }
            ],
        }

        common = from_seer_smap(smap, raw_ref="seer-client/sam_sdc_test_1.smap")

        self.assertEqual(common["schemaVersion"], "uamap.core.v1")
        self.assertEqual(common["map"]["mapId"], "sam_sdc_test_1")
        self.assertEqual(common["map"]["version"], "1.0.6")
        self.assertEqual(common["coordinateSystem"]["sourceUnit"], "m")

        nodes = _by_id(common["graph"]["nodes"])
        self.assertEqual(nodes["LM3"]["kind"], "waypoint")
        self.assertEqual(nodes["LM3"]["pose"], {"x": 2.271, "y": -1.748, "theta": -0.0035, "mapId": "sam_sdc_test_1"})

        edges = _by_id(common["graph"]["edges"])
        self.assertEqual(edges["LM3-LM4"]["fromNodeId"], "LM3")
        self.assertEqual(edges["LM3-LM4"]["toNodeId"], "LM4")
        self.assertEqual(edges["LM3-LM4"]["geometry"]["type"], "bezier")
        self.assertEqual(edges["LM3-LM4"]["geometry"]["controlPoints"][0], {"x": 3.382, "y": -1.754})

        self.assertEqual(common["layers"]["featureLines"][0]["start"], {"x": 5.41, "y": 0.02})
        self.assertEqual(common["layers"]["occupancy"]["pointCount"], 2)
        self.assertEqual(common["layers"]["occupancy"]["sourceRef"]["path"], "normalPosList")


class CommonMapWarningsTest(unittest.TestCase):
    def test_duplicate_node_id_keeps_first_and_warns(self):
        # Two source objects with the same name must not produce two graph
        # nodes sharing an id; keep the first and record a warning instead of
        # silently emitting a duplicate.
        snapshot = {
            "mapId": "lab2m",
            "raw": {
                "Header": "umcl-map",
                "Objs": {
                    "Goal": [{"name": "X", "pose": "1000 1000 0.00"}],
                    "Dock": [{"name": "X", "pose": "2000 2000 0.00"}],
                },
            },
        }

        common = from_jibot_snapshot(snapshot)

        self.assertEqual([n["id"] for n in common["graph"]["nodes"]], ["X"])
        self.assertEqual(common["graph"]["nodes"][0]["kind"], "goal")
        self.assertIn("duplicateNodeId", [w["code"] for w in common["warnings"]])

    def test_jibot_unsupported_objs_category_is_warned(self):
        # Unmapped JIBOT Objs categories must surface as a warning rather than
        # vanishing silently.
        snapshot = {
            "mapId": "lab2m",
            "raw": {"Header": "umcl-map", "Objs": {"MysteryThing": [{"name": "z"}]}},
        }

        common = from_jibot_snapshot(snapshot)

        self.assertIn(
            "unsupportedJibotCategory", [w["code"] for w in common["warnings"]]
        )

    def test_seer_non_locationmark_point_is_warned_not_silent(self):
        # A SEER advancedPointList entry that is not a LocationMark is skipped,
        # but the skip must be recorded so it is not a silent data loss.
        smap = {
            "header": {"mapName": "m"},
            "advancedPointList": [
                {
                    "className": "ChargePoint",
                    "instanceName": "CP1",
                    "pos": {"x": 1.0, "y": 2.0},
                    "dir": 0.0,
                }
            ],
        }

        common = from_seer_smap(smap)

        self.assertEqual(common["graph"]["nodes"], [])
        self.assertIn(
            "unsupportedSeerPointClass", [w["code"] for w in common["warnings"]]
        )

    def test_seer_duplicate_node_id_keeps_first_and_warns(self):
        smap = {
            "header": {"mapName": "m"},
            "advancedPointList": [
                {"className": "LocationMark", "instanceName": "LM1", "pos": {"x": 0.0, "y": 0.0}, "dir": 0.0},
                {"className": "LocationMark", "instanceName": "LM1", "pos": {"x": 9.0, "y": 9.0}, "dir": 0.0},
            ],
        }

        common = from_seer_smap(smap)

        self.assertEqual([n["id"] for n in common["graph"]["nodes"]], ["LM1"])
        self.assertEqual(common["graph"]["nodes"][0]["pose"]["x"], 0.0)
        self.assertIn("duplicateNodeId", [w["code"] for w in common["warnings"]])

    def test_jibot_duplicate_edge_id_keeps_first_and_warns(self):
        # A vertex list that names the same target twice must not emit two
        # edges sharing an id.
        snapshot = {
            "mapId": "lab2m",
            "raw": {
                "Header": "umcl-map",
                "Objs": {
                    "PathPoint": [
                        {"name": "p1", "pose": "0 0 0.00", "vertex": "p2 p2", "costs": [1, 5]},
                        {"name": "p2", "pose": "1000 0 0.00", "vertex": ""},
                    ]
                },
            },
        }

        common = from_jibot_snapshot(snapshot)

        self.assertEqual([e["id"] for e in common["graph"]["edges"]], ["p1->p2"])
        self.assertEqual(common["graph"]["edges"][0]["cost"], 1)
        self.assertIn("duplicateEdgeId", [w["code"] for w in common["warnings"]])

    def test_seer_duplicate_edge_id_keeps_first_and_warns(self):
        smap = {
            "header": {"mapName": "m"},
            "advancedCurveList": [
                {"instanceName": "E1", "startPos": {"instanceName": "A"}, "endPos": {"instanceName": "B"}},
                {"instanceName": "E1", "startPos": {"instanceName": "A"}, "endPos": {"instanceName": "B"}},
            ],
        }

        common = from_seer_smap(smap)

        self.assertEqual([e["id"] for e in common["graph"]["edges"]], ["E1"])
        self.assertIn("duplicateEdgeId", [w["code"] for w in common["warnings"]])


class JibotThetaUnitTest(unittest.TestCase):
    """G4 theta unit: JIBOT native theta is DEGREES; canonical pose.theta must be RADIANS."""

    def _make_snapshot(self, pose_str: str) -> dict:
        return {
            "mapId": "test-map",
            "raw": {
                "Header": "umcl-map",
                "Objs": {
                    "Goal": [{"name": "N1", "pose": pose_str}],
                },
            },
        }

    def test_zero_heading_stays_zero(self):
        """0 degrees → 0 radians (no change, sanity check)."""
        common = from_jibot_snapshot(self._make_snapshot("1000 2000 0.0"))
        node = common["graph"]["nodes"][0]
        self.assertAlmostEqual(node["pose"]["theta"], 0.0, places=6)

    def test_ninety_degree_heading_converted_to_radians(self):
        """90.0 deg in source → math.radians(90) ≈ 1.5708 in canonical pose.theta."""
        common = from_jibot_snapshot(self._make_snapshot("1000 2000 90.0"))
        node = common["graph"]["nodes"][0]
        self.assertAlmostEqual(node["pose"]["theta"], math.radians(90.0), places=4)

    def test_source_pose_preserves_raw_degrees(self):
        """sourcePose.theta must still hold the raw degree value (90.0), not radians."""
        common = from_jibot_snapshot(self._make_snapshot("1000 2000 90.0"))
        node = common["graph"]["nodes"][0]
        self.assertAlmostEqual(node["sourcePose"]["theta"], 90.0, places=4)

    def test_coordinate_system_source_theta_unit_is_deg(self):
        """coordinateSystem.sourceThetaUnit must be 'deg' (JIBOT native is degrees)."""
        common = from_jibot_snapshot(self._make_snapshot("1000 2000 0.0"))
        self.assertEqual(common["coordinateSystem"]["sourceThetaUnit"], "deg")

    def test_coordinate_system_theta_unit_is_rad(self):
        """coordinateSystem.thetaUnit must remain 'rad' (canonical is radians)."""
        common = from_jibot_snapshot(self._make_snapshot("1000 2000 0.0"))
        self.assertEqual(common["coordinateSystem"]["thetaUnit"], "rad")

    def test_goal_with_heading_ninety_deg_converted(self):
        """GoalWithHeading with 90.0 deg heading → node pose.theta ≈ 1.5708."""
        snapshot = {
            "mapId": "test-map",
            "raw": {
                "Header": "umcl-map",
                "Objs": {
                    "GoalWithHeading": [{"name": "GH1", "pose": "1000 1000 90.0"}],
                },
            },
        }
        common = from_jibot_snapshot(snapshot)
        node = common["graph"]["nodes"][0]
        self.assertEqual(node["kind"], "goal")
        self.assertAlmostEqual(node["pose"]["theta"], math.radians(90.0), places=4)

    def test_dock_one_eighty_deg_converted(self):
        """Dock at 180.0 deg → pose.theta ≈ 3.14159."""
        snapshot = {
            "mapId": "test-map",
            "raw": {
                "Header": "umcl-map",
                "Objs": {
                    "Dock": [{"name": "D1", "pose": "5000 6000 180.0"}],
                },
            },
        }
        common = from_jibot_snapshot(snapshot)
        node = common["graph"]["nodes"][0]
        self.assertAlmostEqual(node["pose"]["theta"], math.pi, places=4)

    def test_path_point_theta_converted(self):
        """PathPoint with non-zero theta is also converted deg→rad."""
        snapshot = {
            "mapId": "test-map",
            "raw": {
                "Header": "umcl-map",
                "Objs": {
                    "PathPoint": [
                        {"name": "PP1", "pose": "1000 1000 45.0", "vertex": ""},
                    ],
                },
            },
        }
        common = from_jibot_snapshot(snapshot)
        node = common["graph"]["nodes"][0]
        self.assertAlmostEqual(node["pose"]["theta"], math.radians(45.0), places=4)


if __name__ == "__main__":
    unittest.main()
