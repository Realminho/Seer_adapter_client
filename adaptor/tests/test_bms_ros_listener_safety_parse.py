import unittest

from bms_ros_listener import find_robot_status_columns, parse_robot_safety_row

HEADER = (
    "%time,field.system_status,field.motor_enable,field.motor_enable_status,field.hmi_estop,"
    "field.bumpe_stop,field.pc_estop,field.motor_error,field.pc_enable,"
    "field.bms_voltage,field.bms_current,field.charge"
)


class SafetyColumnsTest(unittest.TestCase):
    def test_finds_safety_columns_by_suffix(self):
        cols = find_robot_status_columns(HEADER)
        self.assertEqual(cols["system_status"], 1)
        self.assertEqual(cols["motor_enable_status"], 3)
        self.assertEqual(cols["hmi_estop"], 4)
        self.assertEqual(cols["motor_error"], 7)
        self.assertEqual(cols["charge"], 11)
        self.assertNotIn("bms_voltage", cols)  # only safety fields

    def test_parse_extracts_values(self):
        cols = find_robot_status_columns(HEADER)
        row = '1700000000000000000,Press ON to Enable.,0,1,0,1,0,0,1,52.9,-0.8,0'
        out = parse_robot_safety_row(row, cols)
        self.assertEqual(out["system_status"], "Press ON to Enable.")
        self.assertEqual(out["motor_enable"], "0")
        self.assertEqual(out["motor_enable_status"], "1")
        self.assertEqual(out["bumpe_stop"], "1")
        self.assertEqual(out["hmi_estop"], "0")

    def test_parse_handles_quoted_comma_in_status(self):
        cols = find_robot_status_columns(HEADER)
        row = '1700000000000000000,"Stopped, waiting",1,0,0,0,0,0,1,52.9,-0.8,0'
        out = parse_robot_safety_row(row, cols)
        self.assertEqual(out["system_status"], "Stopped, waiting")

    def test_parse_short_row_returns_empty(self):
        cols = find_robot_status_columns(HEADER)
        self.assertEqual(parse_robot_safety_row("1,2", cols), {})


class NewStatusFieldsTest(unittest.TestCase):
    HEADER = (
        "%time,field.system_status,field.system_error_code,field.motor_enable,"
        "field.motor_error,field.l_status,field.l_error,field.r_status,"
        "field.r_error,field.lift_status,field.rotate_status,field.charge"
    )

    def test_new_fields_extracted_to_distinct_columns(self):
        cols = find_robot_status_columns(self.HEADER)
        for field in (
            "system_error_code", "l_status", "l_error",
            "r_status", "r_error", "lift_status", "rotate_status",
        ):
            self.assertIn(field, cols, f"{field} not mapped")
        # r_error must NOT collide with the motor_error column.
        self.assertNotEqual(cols["r_error"], cols["motor_error"])
        self.assertNotEqual(cols["l_error"], cols["r_error"])

    def test_row_values_parsed(self):
        cols = find_robot_status_columns(self.HEADER)
        row = "1700000000.0,Normal,200,1,0,0,0,1,0,0,0,0"
        parsed = parse_robot_safety_row(row, cols)
        self.assertEqual(parsed["system_error_code"], "200")
        self.assertEqual(parsed["r_status"], "1")
        self.assertEqual(parsed["motor_error"], "0")


if __name__ == "__main__":
    unittest.main()
