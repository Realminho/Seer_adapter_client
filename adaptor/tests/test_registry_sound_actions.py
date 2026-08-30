import unittest

from core.registry import _JIBOT_INSTANT_ACTIONS


class RegistrySoundActionsTest(unittest.TestCase):
    def test_jibot_exposes_sound_test_buttons(self):
        by_type = {a.action_type: a for a in _JIBOT_INSTANT_ACTIONS}
        self.assertIn("testSound", by_type)
        self.assertIn("stopSound", by_type)
        # parameterless buttons go through the default instant-action POST path
        self.assertFalse(by_type["testSound"].motion)
        self.assertFalse(by_type["stopSound"].motion)

    def test_jibot_exposes_set_sound_volume(self):
        # setSoundVolume must be registered so the WebUI volume control renders
        # and the /action POST allowlist accepts it (server rejects unknown types).
        by_type = {a.action_type: a for a in _JIBOT_INSTANT_ACTIONS}
        self.assertIn("setSoundVolume", by_type)
        # volume change is not a robot motion -> no confirm keystroke required.
        self.assertFalse(by_type["setSoundVolume"].motion)


if __name__ == "__main__":
    unittest.main()
