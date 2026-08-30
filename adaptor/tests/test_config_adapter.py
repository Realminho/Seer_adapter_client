import unittest

from config.config import AdapterConfig, AdapterInstance, _adapter_from_dict, get_config


class AdapterConfigParseTest(unittest.TestCase):
    def test_empty_defaults_to_jibot(self):
        adapter = _adapter_from_dict({})
        self.assertEqual(adapter.vendor, "jibot")
        self.assertEqual(adapter.instances, [])

    def test_vendor_only(self):
        adapter = _adapter_from_dict({"vendor": "hexplorer"})
        self.assertEqual(adapter.vendor, "hexplorer")
        self.assertEqual(adapter.instances, [])

    def test_instances_parsed(self):
        adapter = _adapter_from_dict(
            {
                "vendor": "jibot",
                "instances": [
                    {"name": "line1", "vendor": "hexplorer", "config": "config/line1.toml"},
                    {"name": "line2"},
                ],
            }
        )
        self.assertEqual(
            adapter.instances,
            [
                AdapterInstance(name="line1", vendor="hexplorer", config="config/line1.toml"),
                AdapterInstance(name="line2", vendor="jibot", config=None),
            ],
        )

    def test_real_config_has_adapter_section(self):
        cfg = get_config()
        self.assertIsInstance(cfg.adapter, AdapterConfig)
        self.assertEqual(cfg.adapter.vendor, "jibot")


if __name__ == "__main__":
    unittest.main()
