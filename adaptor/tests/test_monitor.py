"""Tests for core.monitor schema-tolerant state extraction."""

import unittest

from core import monitor


class ExtractStateTest(unittest.TestCase):
    def test_primary_schema(self):
        out = monitor.extract_state(
            {
                "headerId": 9,
                "operatingMode": "AUTOMATIC",
                "driving": True,
                "paused": False,
                "lastNodeId": "N1",
                "mobileRobotPosition": {
                    "x": 1.0,
                    "y": 2.0,
                    "theta": 30.0,
                    "mapId": "lab2m",
                    "localizationScore": 95.0,
                },
                "powerSupply": {"stateOfCharge": 80.0, "charging": True},
                "safetyState": {
                    "activeEmergencyStop": "MANUAL",
                    "fieldViolation": False,
                },
                "errors": [{"errorType": "X"}],
            }
        )
        self.assertEqual(out["operating_mode"], "AUTOMATIC")
        self.assertTrue(out["driving"])
        self.assertEqual(out["x"], 1.0)
        self.assertEqual(out["map_id"], "lab2m")
        self.assertEqual(out["localization_score"], 95.0)
        self.assertEqual(out["battery_soc"], 80.0)
        self.assertTrue(out["charging"])
        self.assertEqual(out["active_emergency_stop"], "MANUAL")
        self.assertFalse(out["field_violation"])
        self.assertEqual(len(out["errors"]), 1)

    def test_standard_vda5050_schema(self):
        # agvPosition / batteryState are the upstream VDA5050 names.
        out = monitor.extract_state(
            {
                "agvPosition": {"x": 5.0, "y": 6.0, "theta": 0.0, "mapId": "m"},
                "batteryState": {"stateOfCharge": 33.0, "charging": False},
            }
        )
        self.assertEqual(out["x"], 5.0)
        self.assertEqual(out["battery_soc"], 33.0)
        self.assertFalse(out["charging"])

    def test_instant_action_states_parsed(self):
        out = monitor.extract_state(
            {
                "instantActionStates": [
                    {
                        "actionId": "a1",
                        "actionStatus": "FAILED",
                        "actionType": "clamp",
                        "resultDescription": "busy",
                    }
                ]
            }
        )
        self.assertEqual(len(out["instant_action_states"]), 1)
        self.assertEqual(out["instant_action_states"][0]["actionStatus"], "FAILED")

    def test_top_level_localization_score(self):
        out = monitor.extract_state({"localizationScore": 70.0})
        self.assertEqual(out["localization_score"], 70.0)

    def test_missing_fields_are_omitted(self):
        out = monitor.extract_state({"headerId": 1})
        self.assertNotIn("operating_mode", out)
        self.assertNotIn("x", out)
        self.assertEqual(out["header_id"], 1)

    def test_non_dict_payload(self):
        self.assertEqual(monitor.extract_state(["not", "a", "dict"]), {})

    def test_numeric_fields_coerced_from_strings(self):
        # A rogue/malformed publisher sending numbers-as-strings must not poison
        # the snapshot with values the dashboard would crash formatting.
        out = monitor.extract_state(
            {
                "mobileRobotPosition": {"x": "1.5", "y": "2", "theta": "abc"},
                "powerSupply": {"stateOfCharge": "85"},
                "localizationScore": "90.0",
            }
        )
        self.assertEqual(out["x"], 1.5)
        self.assertEqual(out["y"], 2.0)
        self.assertIsNone(out["theta"])  # non-numeric -> None, not "abc"
        self.assertEqual(out["battery_soc"], 85.0)
        self.assertEqual(out["localization_score"], 90.0)
        for value in (out["x"], out["y"], out["battery_soc"], out["localization_score"]):
            self.assertIsInstance(value, float)

    def test_bool_is_not_treated_as_number(self):
        out = monitor.extract_state({"powerSupply": {"stateOfCharge": True}})
        self.assertIsNone(out["battery_soc"])

    def test_negative_state_of_charge_is_unknown(self):
        # The adapter publishes stateOfCharge = -1 as an "unknown" sentinel when
        # the JIBOT link is down (VDA5050 requires a numeric field, so null is
        # not possible). The dashboard must treat any negative SoC as unknown so
        # _num_or_dash renders "—", never a literal "-1%".
        out = monitor.extract_state(
            {"powerSupply": {"stateOfCharge": -1, "charging": False}}
        )
        self.assertIsNone(out["battery_soc"])

    def test_order_and_path_fields(self):
        out = monitor.extract_state(
            {
                "orderId": "ORD-7",
                "orderUpdateId": 3,
                "lastNodeId": "N1",
                "nodeStates": [
                    {"nodeId": "N3", "sequenceId": 4, "released": True},
                    {"nodeId": "N2", "sequenceId": 2, "released": True},
                ],
                "edgeStates": [{"edgeId": "E1", "sequenceId": 1, "released": True}],
                "actionStates": [
                    {"actionId": "a1", "actionStatus": "RUNNING"},
                    {"actionId": "a2", "actionStatus": "WAITING"},
                ],
            }
        )
        self.assertEqual(out["order_id"], "ORD-7")
        self.assertEqual(out["order_update_id"], 3)
        self.assertEqual(len(out["node_states"]), 2)
        self.assertEqual(len(out["edge_states"]), 1)
        self.assertEqual(len(out["action_states"]), 2)

    def test_empty_order_lists_clear_state(self):
        # A finished/idle order publishes empty lists; these must overwrite,
        # not be dropped, so the dashboard can clear a completed order.
        out = monitor.extract_state(
            {"orderId": "", "nodeStates": [], "edgeStates": [], "actionStates": []}
        )
        self.assertEqual(out["order_id"], "")
        self.assertEqual(out["node_states"], [])
        self.assertEqual(out["edge_states"], [])
        self.assertEqual(out["action_states"], [])

    def test_information_jibot_motor_state_parsed(self):
        out = monitor.extract_state(
            {
                "information": [
                    {
                        "infoType": "JIBOT_STATUS",
                        "infoReferences": [
                            {"referenceKey": "jibotMode", "referenceValue": "auto"},
                            {"referenceKey": "jibotMotorState", "referenceValue": "stopped"},
                        ],
                    }
                ]
            }
        )
        self.assertEqual(out["motor_state"], "stopped")

    def test_motor_state_omitted_when_absent(self):
        out = monitor.extract_state({"information": [{"infoType": "JIBOT_STATUS", "infoReferences": []}]})
        self.assertNotIn("motor_state", out)
        self.assertNotIn("motor_state", monitor.extract_state({"headerId": 1}))

    def test_information_working_state_parsed(self):
        # Adapter publishes workingState in the AMR_STATE info block; the WebUi
        # status strip reads it back from the snapshot.
        out = monitor.extract_state(
            {
                "information": [
                    {
                        "infoType": "AMR_STATE",
                        "infoReferences": [
                            {"referenceKey": "workingState", "referenceValue": "CHARGING"},
                            {"referenceKey": "workingStateDetail", "referenceValue": "DOCK_CHARGE"},
                        ],
                    }
                ]
            }
        )
        self.assertEqual(out["working_state"], "CHARGING")

    def test_information_active_action_and_step_parsed(self):
        # 진행 화면이 "지금 무슨 액션 / 무슨 step"을 이 두 키로 읽는다.
        out = monitor.extract_state(
            {
                "information": [
                    {
                        "infoType": "AMR_STATE",
                        "infoReferences": [
                            {"referenceKey": "workingState", "referenceValue": "ACTING"},
                            {"referenceKey": "workingStateDetail", "referenceValue": "LOADING"},
                            {"referenceKey": "activeActionType", "referenceValue": "clampMoveTo"},
                            {"referenceKey": "activeStepActionType", "referenceValue": "pioWriteOut"},
                        ],
                    }
                ]
            }
        )
        self.assertEqual(out["working_state_detail"], "LOADING")
        self.assertEqual(out["active_action_type"], "clampMoveTo")
        self.assertEqual(out["active_step_action_type"], "pioWriteOut")

    def test_active_action_fields_omitted_when_absent(self):
        out = monitor.extract_state({"headerId": 1})
        self.assertNotIn("working_state_detail", out)
        self.assertNotIn("active_action_type", out)
        self.assertNotIn("active_step_action_type", out)

    def test_working_state_omitted_when_absent(self):
        self.assertNotIn("working_state", monitor.extract_state({"headerId": 1}))

    def test_order_fields_omitted_when_absent(self):
        out = monitor.extract_state({"headerId": 1})
        self.assertNotIn("order_id", out)
        self.assertNotIn("node_states", out)

    def test_non_list_path_ignored(self):
        out = monitor.extract_state({"nodeStates": "oops", "actionStates": None})
        self.assertNotIn("node_states", out)
        self.assertNotIn("action_states", out)

    def test_extract_connection(self):
        self.assertEqual(monitor.extract_connection({"connectionState": "ONLINE"}), "ONLINE")
        self.assertIsNone(monitor.extract_connection({}))
        self.assertIsNone(monitor.extract_connection("x"))


class SnapshotTest(unittest.TestCase):
    def test_age(self):
        snap = monitor.StateSnapshot()
        self.assertIsNone(snap.age(100.0))
        snap.state_ts = 90.0
        self.assertEqual(snap.age(100.0), 10.0)
        # clamps negatives (clock skew) to 0
        self.assertEqual(snap.age(80.0), 0.0)


class ExtractIoTest(unittest.TestCase):
    def test_parses_ezio_and_pio_blocks(self):
        io = monitor.extract_io({
            "updated_at": 100.0,
            "ezio": {
                "configured": True, "ip": "10.8.8.87", "port": 3002,
                "connected": True, "board": "EZI-IO X",
                "inputs": [0, 1, 0], "inputs_updated_at": 99.0,
                "outputs": [1, 0], "outputs_updated_at": 98.0, "error": "",
            },
            "pio": {
                "configured": True, "port": "/dev/ttyUSB0", "baudrate": 19200,
                "connected": False, "inputs": {"1": "on"},
                "inputs_updated_at": 50.0,
                "outputs": {"2": "on"}, "outputs_updated_at": 51.0, "error": "",
            },
        })
        self.assertEqual(io.updated_at, 100.0)
        self.assertTrue(io.ezio.configured)
        self.assertEqual(io.ezio.ip, "10.8.8.87")
        self.assertEqual(io.ezio.inputs, [0, 1, 0])
        self.assertEqual(io.ezio.outputs_updated_at, 98.0)
        self.assertTrue(io.pio.configured)
        self.assertFalse(io.pio.connected)
        self.assertEqual(io.pio.inputs, {"1": "on"})
        self.assertEqual(io.pio.inputs_updated_at, 50.0)
        self.assertEqual(io.pio.outputs, {"2": "on"})
        self.assertEqual(io.pio.outputs_updated_at, 51.0)

    def test_missing_blocks_and_non_dict_are_tolerated(self):
        self.assertIsNone(monitor.extract_io({}).ezio)
        self.assertIsNone(monitor.extract_io({}).pio)
        empty = monitor.extract_io("nope")
        self.assertIsNone(empty.updated_at)
        self.assertIsNone(empty.ezio)


if __name__ == "__main__":
    unittest.main()
