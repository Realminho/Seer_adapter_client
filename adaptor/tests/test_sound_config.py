import unittest

from config.config import SoundSettings


class SoundSettingsTest(unittest.TestCase):
    def test_defaults_apply_when_only_legacy_keys_given(self):
        s = SoundSettings(
            travel_music="travel.mp3",
            work_music="work.mp3",
            route_prefiX=["A_", "B_"],
        )
        self.assertTrue(s.enabled)
        self.assertEqual(s.sink, "")
        self.assertEqual(s.sound_dir, "sounds")
        self.assertEqual(s.player, "mplayer")
        self.assertIsNone(s.startup_volume)

    def test_constructs_with_no_args(self):
        # All fields are optional now (files matched by convention, not config).
        s = SoundSettings()
        self.assertTrue(s.enabled)
        self.assertEqual(s.sound_dir, "sounds")
        self.assertEqual(s.player, "mplayer")
        self.assertEqual(s.route_prefiX, [])

    def test_new_keys_override_defaults(self):
        s = SoundSettings(
            travel_music="t.mp3",
            work_music="w.mp3",
            route_prefiX=[],
            enabled=False,
            sink="sink0",
            sound_dir="/srv/sounds",
            player="mpg123",
            startup_volume=55,
        )
        self.assertFalse(s.enabled)
        self.assertEqual(s.sink, "sink0")
        self.assertEqual(s.sound_dir, "/srv/sounds")
        self.assertEqual(s.player, "mpg123")
        self.assertEqual(s.startup_volume, 55)


if __name__ == "__main__":
    unittest.main()
