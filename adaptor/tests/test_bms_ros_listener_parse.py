import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # adaptor/ on path

from bms_ros_listener import find_bms_columns, parse_bms_row

HEADER = "%time,field.charge,field.EQ,field.bms_voltage,field.bms_current,field.system_status"


class FindBmsColumnsTest(unittest.TestCase):
    def test_locates_columns_by_suffix(self):
        self.assertEqual(find_bms_columns(HEADER), (3, 4))

    def test_order_independent(self):
        header = "%time,field.bms_current,field.bms_voltage"
        self.assertEqual(find_bms_columns(header), (2, 1))

    def test_missing_columns_return_none(self):
        self.assertEqual(find_bms_columns("%time,field.charge,field.EQ"), (None, None))


class ParseBmsRowTest(unittest.TestCase):
    def test_parses_floats(self):
        self.assertEqual(parse_bms_row("1700000000,1,88,54.6,7.2,Normal", 3, 4), (54.6, 7.2))

    def test_signed_current_preserved(self):
        self.assertEqual(parse_bms_row("1700000000,0,77,53.0,-0.4,Normal", 3, 4), (53.0, -0.4))

    def test_short_row_returns_none(self):
        self.assertIsNone(parse_bms_row("1700000000,0,77", 3, 4))

    def test_non_numeric_returns_none(self):
        self.assertIsNone(parse_bms_row("1700000000,0,77,n/a,x,Normal", 3, 4))

    def test_none_index_returns_none(self):
        self.assertIsNone(parse_bms_row("a,b,c", None, 4))


if __name__ == "__main__":
    unittest.main()
