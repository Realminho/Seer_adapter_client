"""G6 golden cross-repo contract test for uamap.core.v1 (adaptor side).

Asserts that from_jibot_snapshot(canonical_jibot_raw.json) produces output
byte-equal to golden.uamap.json.  Any drift in the producer (common_amr_map)
will break this test, surfacing silent schema changes before they reach WCS.
"""

import json
import sys
import unittest
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parents[1]
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from common_amr_map import from_jibot_snapshot

FIXTURES = Path(__file__).parent / "fixtures"
CANONICAL_INPUT = FIXTURES / "canonical_jibot_raw.json"
GOLDEN_OUTPUT = FIXTURES / "golden.uamap.json"


def _load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump_sorted(obj) -> str:
    """Deterministic serialisation for deep-equal diff display."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


class ContractGoldenTest(unittest.TestCase):
    """Adaptor side of the G6 cross-repo contract gate."""

    def test_from_jibot_snapshot_matches_golden(self):
        """from_jibot_snapshot(canonical_jibot_raw) must deep-equal golden.uamap.json."""
        snapshot = _load_json(CANONICAL_INPUT)
        golden = _load_json(GOLDEN_OUTPUT)

        actual = from_jibot_snapshot(snapshot)

        # Deep-equal comparison with human-readable diff on failure
        self.assertEqual(
            _dump_sorted(actual),
            _dump_sorted(golden),
            "from_jibot_snapshot output diverged from golden — "
            "update golden.uamap.json if the change is intentional, "
            "then copy it to wcs packages/api/src/nestjs/amr-map/__fixtures__/contract.golden.uamap.json",
        )

    def test_golden_schema_version(self):
        """Golden must carry schemaVersion == 'uamap.core.v1'."""
        golden = _load_json(GOLDEN_OUTPUT)
        self.assertEqual(golden["schemaVersion"], "uamap.core.v1")

    def test_golden_node_kinds_are_known(self):
        """Every node kind in the golden must be within the known WCS UamapNodeKind set.

        This is the drift guard: if the producer renames a kind (e.g. 'goal' →
        'goalPoint'), the consumer (WCS) will silently fall through to the
        WAYPOINT default in KIND_TO_TARGET_TYPE.  Catching it here forces an
        explicit update of both sides.
        """
        # Mirror of WCS uamap.types.ts UamapNodeKind values
        KNOWN_NODE_KINDS = {"goal", "dock", "charger", "station", "waypoint", "pathPoint"}
        golden = _load_json(GOLDEN_OUTPUT)
        for node in golden["graph"]["nodes"]:
            self.assertIn(
                node["kind"],
                KNOWN_NODE_KINDS,
                f"Node '{node['id']}' has kind '{node['kind']}' which is not in "
                f"WCS UamapNodeKind — update both producer and consumer",
            )

    def test_golden_has_expected_structure(self):
        """Golden must contain the expected node/edge/zone counts from the canonical input."""
        golden = _load_json(GOLDEN_OUTPUT)
        nodes = golden["graph"]["nodes"]
        edges = golden["graph"]["edges"]
        zones = golden["zones"]

        node_ids = {n["id"] for n in nodes}
        # canonical input: 2 Goals + 1 Dock + 2 PathPoints = 5 nodes
        self.assertEqual(len(nodes), 5, f"Expected 5 nodes, got {len(nodes)}: {node_ids}")
        # canonical input: 1 edge (PP1->PP2)
        self.assertEqual(len(edges), 1, f"Expected 1 edge, got {len(edges)}")
        self.assertEqual(edges[0]["id"], "PP1->PP2")
        # canonical input: 1 AvoidArea zone
        self.assertEqual(len(zones), 1, f"Expected 1 zone, got {len(zones)}")
        self.assertEqual(zones[0]["kind"], "avoid")

        # Goal nodes use kind 'goal'
        goal_nodes = [n for n in nodes if n["kind"] == "goal"]
        self.assertEqual(len(goal_nodes), 2)
        # Dock node uses kind 'dock'
        dock_nodes = [n for n in nodes if n["kind"] == "dock"]
        self.assertEqual(len(dock_nodes), 1)
        # PathPoint nodes use kind 'pathPoint'
        pp_nodes = [n for n in nodes if n["kind"] == "pathPoint"]
        self.assertEqual(len(pp_nodes), 2)


if __name__ == "__main__":
    unittest.main()
