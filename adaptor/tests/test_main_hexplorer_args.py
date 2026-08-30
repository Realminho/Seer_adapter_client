import unittest
from unittest.mock import patch

import adapter_hexplorer
from main_hexplorer import _parse_args


class HexplorerArgsTest(unittest.TestCase):
    def test_config_flag_parsed(self):
        self.assertEqual(_parse_args(["--config", "config/line1.toml"]).config, "config/line1.toml")

    def test_config_defaults_to_none(self):
        self.assertIsNone(_parse_args([]).config)


class HexplorerAdapterConfigPathTest(unittest.TestCase):
    def test_config_path_forwarded_to_get_config(self):
        # Real default config so __init__ has all the fields it reads, but assert
        # the path argument is forwarded. client=object() avoids a real client.
        real = adapter_hexplorer.get_config()
        with patch.object(adapter_hexplorer, "get_config", return_value=real) as gc:
            adapter_hexplorer.HexplorerAdapter(client=object(), config_path="config/line1.toml")
        gc.assert_called_once_with(config_path="config/line1.toml")


if __name__ == "__main__":
    unittest.main()
