import unittest

from utils.jibot_error_catalog import decode_system_error_code, ERROR_CODE_CATALOG


class JibotErrorCatalogTest(unittest.TestCase):
    def test_known_code_200_goal_not_in_map(self):
        name, desc = decode_system_error_code("200")
        self.assertEqual(name, "ERROR0200")
        self.assertEqual(desc, "robot do not find goal name in map")

    def test_zero_is_no_error(self):
        self.assertEqual(decode_system_error_code("0"), ("", ""))
        self.assertEqual(decode_system_error_code(0), ("", ""))

    def test_empty_or_none_is_no_error(self):
        self.assertEqual(decode_system_error_code(""), ("", ""))
        self.assertEqual(decode_system_error_code(None), ("", ""))

    def test_non_integer_is_safe(self):
        self.assertEqual(decode_system_error_code("oops"), ("", ""))

    def test_unknown_code_keeps_name_blank_desc(self):
        self.assertEqual(decode_system_error_code("9999"), ("ERROR9999", ""))

    def test_int_input_accepted(self):
        name, desc = decode_system_error_code(702)
        self.assertEqual(name, "ERROR0702")
        self.assertEqual(desc, "robot main loop stuck")

    def test_catalog_has_core_codes(self):
        for code in (200, 201, 600, 603, 700, 701, 702, 510):
            self.assertIn(code, ERROR_CODE_CATALOG)


if __name__ == "__main__":
    unittest.main()
