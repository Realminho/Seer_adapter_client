"""to_jibot_snapshot 역변환 + round-trip 충실도.

Inverse of from_jibot_snapshot: uamap (canonical, m, rad) → JIBOT raw dict (mm, deg).
"""
import math
import unittest
import sys
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from common_amr_map import from_jibot_snapshot, to_jibot_snapshot

RAW = {  # 비-0 heading 포함
    "Header": "umcl-map",
    "MapName": "lab2m",
    "MapRes": 20,
    "MinPose": "0 0",
    "MaxPose": "20000 10000",
    "Objs": {
        "Goal": [{"name": "G1", "pose": "5953 4854 90.0"}],
        "Dock": [{"name": "D1", "pose": "7290 7268 0.0"}],
        "PathPoint": [
            {"name": "p1", "pose": "1000 1000 0.0", "vertex": "p2", "costs": [1]},
            {"name": "p2", "pose": "2000 1000 0.0", "vertex": ""},
        ],
        "AvoidArea": [{"name": "a1", "points": "0 0 1000 1000", "pose": "0 0 0"}],
    },
}


class RoundTripTest(unittest.TestCase):
    def test_from_to_from_fidelity(self):
        u1 = from_jibot_snapshot({"raw": RAW, "mapId": "lab2m"})
        raw2 = to_jibot_snapshot(u1)
        u2 = from_jibot_snapshot({"raw": raw2, "mapId": "lab2m"})
        # 노드 id/kind/좌표(이내 오차) 일치
        n1 = {n["id"]: n for n in u1["graph"]["nodes"]}
        n2 = {n["id"]: n for n in u2["graph"]["nodes"]}
        self.assertEqual(set(n1), set(n2))
        for k in n1:
            self.assertAlmostEqual(n1[k]["pose"]["x"], n2[k]["pose"]["x"], places=3)
            self.assertAlmostEqual(n1[k]["pose"]["y"], n2[k]["pose"]["y"], places=3)
            self.assertAlmostEqual(
                n1[k]["pose"].get("theta", 0), n2[k]["pose"].get("theta", 0), places=4
            )
            self.assertEqual(n1[k]["kind"], n2[k]["kind"])
        self.assertEqual(
            {e["id"] for e in u1["graph"]["edges"]},
            {e["id"] for e in u2["graph"]["edges"]},
        )
        self.assertEqual(len(u1["zones"]), len(u2["zones"]))

    def test_theta_rad_to_deg(self):
        u = from_jibot_snapshot({"raw": RAW, "mapId": "lab2m"})
        raw2 = to_jibot_snapshot(u)
        g1 = next(o for o in raw2["Objs"]["Goal"] if o["name"] == "G1")
        self.assertAlmostEqual(
            float(g1["pose"].split()[2]), 90.0, places=1
        )  # rad→deg 복원


class ToJibotSnapshotStructureTest(unittest.TestCase):
    """Output structure correctness tests for to_jibot_snapshot."""

    def setUp(self):
        self.uamap = from_jibot_snapshot({"raw": RAW, "mapId": "lab2m"})
        self.raw2 = to_jibot_snapshot(self.uamap)

    def test_output_has_required_top_level_keys(self):
        """to_jibot_snapshot output must include Header, MapName, MapRes, MinPose, MaxPose, Objs."""
        for key in ("Header", "MapRes", "MinPose", "MaxPose", "Objs"):
            self.assertIn(key, self.raw2, f"Missing key: {key}")

    def test_objs_has_expected_categories(self):
        """Objs must contain Goal, Dock, PathPoint, AvoidArea."""
        objs = self.raw2["Objs"]
        for cat in ("Goal", "Dock", "PathPoint", "AvoidArea"):
            self.assertIn(cat, objs, f"Missing Objs category: {cat}")

    def test_goal_pose_is_mm(self):
        """Goal pose x/y must be in mm (5953 mm for G1, not 5.953 m)."""
        g1 = next(o for o in self.raw2["Objs"]["Goal"] if o["name"] == "G1")
        parts = g1["pose"].split()
        self.assertAlmostEqual(float(parts[0]), 5953.0, delta=1.0)
        self.assertAlmostEqual(float(parts[1]), 4854.0, delta=1.0)

    def test_dock_pose_format(self):
        """Dock pose must be string 'x y theta'."""
        d1 = next(o for o in self.raw2["Objs"]["Dock"] if o["name"] == "D1")
        parts = d1["pose"].split()
        self.assertEqual(len(parts), 3)

    def test_pathpoint_vertex_string(self):
        """PathPoint vertex must be a space-separated string of target node ids."""
        pp = {o["name"]: o for o in self.raw2["Objs"]["PathPoint"]}
        self.assertIn("p1", pp)
        self.assertIn("p2", pp["p1"]["vertex"])

    def test_pathpoint_costs_list(self):
        """PathPoint costs must be a list of numbers matching vertex targets."""
        pp = {o["name"]: o for o in self.raw2["Objs"]["PathPoint"]}
        self.assertIsInstance(pp["p1"].get("costs"), list)
        self.assertEqual(len(pp["p1"]["costs"]), 1)

    def test_avoid_area_points_format(self):
        """AvoidArea points must be a space-separated string of x/y pairs (mm)."""
        a1 = next(o for o in self.raw2["Objs"]["AvoidArea"] if o["name"] == "a1")
        pts = a1["points"].split()
        # 2-corner bbox → 4 numbers
        self.assertEqual(len(pts), 4)

    def test_mapres_is_mm(self):
        """MapRes must be in mm (20 for 0.02m resolution)."""
        self.assertAlmostEqual(float(self.raw2["MapRes"]), 20.0, delta=0.1)

    def test_minpose_maxpose_strings(self):
        """MinPose/MaxPose must be space-separated strings of x y (mm)."""
        for key in ("MinPose", "MaxPose"):
            val = self.raw2[key]
            self.assertIsInstance(val, str, f"{key} must be a string")
            parts = val.split()
            self.assertGreaterEqual(len(parts), 2)


class GoalWithHeadingRoundTripTest(unittest.TestCase):
    """GoalWithHeading nodes must round-trip back to Objs.GoalWithHeading."""

    RAW_WITH_GWH = {
        "Header": "umcl-map",
        "MapName": "lab2m",
        "MapRes": 20,
        "MinPose": "0 0",
        "MaxPose": "20000 10000",
        "Objs": {
            "Goal": [{"name": "G1", "pose": "5953 4854 90.0"}],
            "GoalWithHeading": [{"name": "GWH1", "pose": "3000 2000 45.0"}],
            "Dock": [],
            "PathPoint": [],
            "AvoidArea": [],
        },
    }

    def test_goalwithheading_roundtrips_to_correct_bucket(self):
        """A GoalWithHeading node must come back in Objs.GoalWithHeading, not Goal."""
        uamap = from_jibot_snapshot({"raw": self.RAW_WITH_GWH, "mapId": "lab2m"})
        raw2 = to_jibot_snapshot(uamap)

        goal_names = {o["name"] for o in raw2["Objs"].get("Goal", [])}
        gwh_names = {o["name"] for o in raw2["Objs"].get("GoalWithHeading", [])}

        self.assertIn("GWH1", gwh_names, "GWH1 must be in Objs.GoalWithHeading after round-trip")
        self.assertNotIn("GWH1", goal_names, "GWH1 must NOT be in Objs.Goal after round-trip")
        self.assertIn("G1", goal_names, "G1 must remain in Objs.Goal")

    def test_goalwithheading_objcategory_not_in_emitted_item(self):
        """The routing hint 'objCategory' must not appear in the emitted JIBOT raw item."""
        uamap = from_jibot_snapshot({"raw": self.RAW_WITH_GWH, "mapId": "lab2m"})
        raw2 = to_jibot_snapshot(uamap)

        for item in raw2["Objs"].get("GoalWithHeading", []):
            self.assertNotIn(
                "objCategory", item,
                f"objCategory routing hint leaked into emitted item: {item}",
            )

    def test_goalwithheading_pose_roundtrips(self):
        """GoalWithHeading pose must survive round-trip within 1 mm / 0.1 deg."""
        uamap = from_jibot_snapshot({"raw": self.RAW_WITH_GWH, "mapId": "lab2m"})
        raw2 = to_jibot_snapshot(uamap)

        gwh_items = {o["name"]: o for o in raw2["Objs"].get("GoalWithHeading", [])}
        self.assertIn("GWH1", gwh_items)
        parts = gwh_items["GWH1"]["pose"].split()
        self.assertAlmostEqual(float(parts[0]), 3000.0, delta=1.0)
        self.assertAlmostEqual(float(parts[1]), 2000.0, delta=1.0)
        self.assertAlmostEqual(float(parts[2]), 45.0, delta=0.1)


if __name__ == "__main__":
    unittest.main()
