import unittest

from seer_client.capabilities import jack_model_capability


class JackModelCapabilityTests(unittest.TestCase):
    def test_roboshop_disabled_jack_is_not_supported(self):
        payload = {
            "modelName": "SBA-400EU",
            "deviceTypes": [{
                "name": "jack",
                "devices": [{
                    "name": "jack",
                    "isEnabled": False,
                    "deviceParams": [{
                        "key": "type",
                        "comboParam": {"childKey": "none"},
                    }],
                }],
            }],
        }
        enabled, reason = jack_model_capability(payload)
        self.assertFalse(enabled)
        self.assertIn("isEnabled=false", reason)

    def test_enabled_jack_device_is_supported_by_model(self):
        payload = {
            "modelName": "AMB-300JZ",
            "deviceTypes": [{
                "name": "jack",
                "devices": [{
                    "name": "jack",
                    "isEnabled": True,
                    "deviceParams": [{
                        "key": "type",
                        "comboParam": {"childKey": "byController"},
                    }],
                }],
            }],
        }
        enabled, reason = jack_model_capability(payload)
        self.assertTrue(enabled)
        self.assertIn("isEnabled=true", reason)

    def test_enabled_but_none_control_is_disabled(self):
        payload = {
            "deviceTypes": [{
                "name": "jack",
                "devices": [{
                    "isEnabled": True,
                    "deviceParams": [{
                        "key": "type",
                        "comboParam": {"childKey": "none"},
                    }],
                }],
            }],
        }
        enabled, _ = jack_model_capability(payload)
        self.assertFalse(enabled)


if __name__ == "__main__":
    unittest.main()
