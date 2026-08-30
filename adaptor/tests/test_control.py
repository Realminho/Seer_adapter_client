"""Tests for tui.control instant-action payload construction.

The payload must round-trip through the adaptor's own
``InstantActions.from_dict`` so the TUI can never publish a message the
adaptor would reject.
"""

import unittest

from core import control
from protocol.vda5050_3_0.messages import InstantActions


class BuildInstantActionsTest(unittest.TestCase):
    def _build(self, **kwargs):
        base = dict(
            header_id=1,
            timestamp="2026-06-11T00:00:00.000Z",
            version="3.0.0",
            manufacturer="jibot",
            serial_number="HN-SH6-TR-001",
            action_type="startPause",
            action_id="tui-1",
        )
        base.update(kwargs)
        return control.build_instant_actions(**base)

    def test_shape_and_header(self):
        payload = self._build()
        self.assertEqual(payload["headerId"], 1)
        self.assertEqual(payload["serialNumber"], "HN-SH6-TR-001")
        self.assertEqual(len(payload["actions"]), 1)
        action = payload["actions"][0]
        self.assertEqual(action["actionType"], "startPause")
        self.assertEqual(action["actionId"], "tui-1")
        self.assertEqual(action["blockingType"], "NONE")
        self.assertEqual(action["actionParameters"], [])

    def test_roundtrips_through_adaptor_parser(self):
        for action_type in (
            "stateRequest",
            "factsheetRequest",
            "startPause",
            "stopPause",
            "cancelOrder",
            "startCharging",
        ):
            payload = self._build(action_type=action_type, action_id=f"tui-{action_type}")
            parsed = InstantActions.from_dict(payload)
            self.assertEqual(parsed.actions[0].action_type, action_type)
            self.assertEqual(parsed.header.serial_number, "HN-SH6-TR-001")

    def test_parameters(self):
        payload = self._build(
            action_type="initPosition",
            parameters=[("x", 1.0), ("y", 2.0), ("theta", 0.0)],
        )
        params = {p["key"]: p["value"] for p in payload["actions"][0]["actionParameters"]}
        self.assertEqual(params, {"x": 1.0, "y": 2.0, "theta": 0.0})
        parsed = InstantActions.from_dict(payload)
        self.assertEqual(len(parsed.actions[0].action_parameters), 3)


if __name__ == "__main__":
    unittest.main()
