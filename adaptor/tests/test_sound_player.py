import os
import threading
import time
import unittest
from unittest import mock

from utils.sound import SoundPlayer


class _FakeProc:
    def __init__(self):
        self._alive = True
        self.terminated = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False
        return 0

    def kill(self):
        self._alive = False


class _WaitBlocksProc(_FakeProc):
    def __init__(self):
        super().__init__()
        self.wait_started = threading.Event()
        self.release_wait = threading.Event()

    def wait(self, timeout=None):
        self.wait_started.set()
        self.release_wait.wait(timeout=1.0)
        self._alive = False
        return 0

    def terminate(self):
        super().terminate()
        self.release_wait.set()


class SoundPlayerTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("utils.sound.os.path.exists", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _player(self, **kw):
        kw.setdefault("sink", "sink0")
        kw.setdefault("sound_dir", "/snd")
        return SoundPlayer(**kw)

    def test_play_spawns_expected_mplayer_command_with_sink_env(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()) as popen:
            p.play("travel.mp3")
        args, kwargs = popen.call_args
        self.assertEqual(
            args[0],
            ["mplayer", "-really-quiet", "-ao", "pulse::sink0", "-loop", "0", "/snd/travel.mp3"],
        )
        self.assertEqual(kwargs["env"]["PULSE_SINK"], "sink0")

    def test_play_empty_sink_uses_pulse_default_output(self):
        p = self._player(sink="")
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()) as popen:
            p.play("travel.mp3")
        args, kwargs = popen.call_args
        self.assertEqual(
            args[0],
            ["mplayer", "-really-quiet", "-ao", "pulse", "-loop", "0", "/snd/travel.mp3"],
        )
        self.assertNotIn("PULSE_SINK", kwargs["env"])

    def test_play_without_loop_omits_loop_flag(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()) as popen:
            p.play("travel.mp3", loop=False)
        self.assertNotIn("-loop", popen.call_args[0][0])

    def test_loop_with_replay_gap_replays_one_shot_under_adapter_control(self):
        p = self._player()
        first = _WaitBlocksProc()
        second = _WaitBlocksProc()
        with mock.patch(
            "utils.sound.subprocess.Popen", side_effect=[first, second]
        ) as popen:
            p.play("travel.mp3", loop=True, replay_gap_sec=0.01)
            self.assertTrue(first.wait_started.wait(timeout=1.0))
            first.release_wait.set()
            self.assertTrue(second.wait_started.wait(timeout=1.0))
            p.stop()
            second.release_wait.set()

        self.assertEqual(popen.call_count, 2)
        self.assertNotIn("-loop", popen.call_args_list[0][0][0])
        self.assertNotIn("-loop", popen.call_args_list[1][0][0])

    def test_same_gap_loop_track_does_not_respawn_while_between_replays(self):
        p = self._player()
        proc = _WaitBlocksProc()
        with mock.patch("utils.sound.subprocess.Popen", return_value=proc) as popen:
            p.play("travel.mp3", loop=True, replay_gap_sec=60.0)
            self.assertTrue(proc.wait_started.wait(timeout=1.0))
            proc.release_wait.set()
            p.play("travel.mp3", loop=True, replay_gap_sec=60.0)
            p.stop()

        self.assertEqual(popen.call_count, 1)

    def test_repeat_count_one_plays_once_without_the_loop_flag(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()) as popen:
            p.play("travel.mp3", repeat_count=1)
        self.assertEqual(popen.call_count, 1)
        self.assertNotIn("-loop", popen.call_args[0][0])

    def test_repeat_count_zero_keeps_the_endless_player_loop(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()) as popen:
            p.play("travel.mp3", repeat_count=0)
        self.assertIn("-loop", popen.call_args[0][0])

    def test_finite_repeat_count_spawns_that_many_plays_then_stops(self):
        p = self._player()
        procs = [_FakeProc(), _FakeProc()]
        with mock.patch("utils.sound.subprocess.Popen", side_effect=procs) as popen:
            p.play("travel.mp3", repeat_count=2)
            for _ in range(200):
                if not p.is_playing():
                    break
                time.sleep(0.01)
        self.assertEqual(popen.call_count, 2)
        self.assertFalse(p.is_playing())
        # Each play is a one-shot process; the count, not mplayer, does the repeat.
        for call in popen.call_args_list:
            self.assertNotIn("-loop", call[0][0])

    def test_repeat_count_one_with_a_gap_still_plays_exactly_once(self):
        """A gap forces the threaded path, which must honour the count too."""
        p = self._player()
        with mock.patch(
            "utils.sound.subprocess.Popen", side_effect=[_FakeProc(), _FakeProc()]
        ) as popen:
            p.play("travel.mp3", replay_gap_sec=0.01, repeat_count=1)
            for _ in range(200):
                if not p.is_playing():
                    break
                time.sleep(0.01)
        self.assertEqual(popen.call_count, 1)
        self.assertFalse(p.is_playing())

    def test_changing_only_the_repeat_count_restarts_playback(self):
        """The dedupe key has to include the count, or a config change is ignored."""
        p = self._player()
        with mock.patch(
            "utils.sound.subprocess.Popen", side_effect=[_FakeProc(), _FakeProc()]
        ) as popen:
            p.play("travel.mp3", repeat_count=0)
            p.play("travel.mp3", repeat_count=1)
        self.assertEqual(popen.call_count, 2)

    def test_repeat_count_is_ignored_for_a_one_shot_call(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()) as popen:
            p.play("travel.mp3", loop=False, repeat_count=5)
        self.assertEqual(popen.call_count, 1)
        self.assertNotIn("-loop", popen.call_args[0][0])

    def test_unusable_repeat_count_falls_back_to_endless(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()) as popen:
            p.play("travel.mp3", repeat_count="oops")
        self.assertIn("-loop", popen.call_args[0][0])

    def test_empty_sink_omits_pulse_sink_env(self):
        p = self._player(sink="")
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()) as popen:
            p.play("t.mp3")
        self.assertNotIn("PULSE_SINK", popen.call_args[1]["env"])

    def test_same_track_does_not_respawn(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()) as popen:
            p.play("travel.mp3")
            p.play("travel.mp3")
        self.assertEqual(popen.call_count, 1)

    def test_different_track_stops_and_respawns(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", side_effect=[_FakeProc(), _FakeProc()]) as popen:
            p.play("travel.mp3")
            first = p._proc
            p.play("work.mp3")
        self.assertTrue(first.terminated)
        self.assertEqual(popen.call_count, 2)

    def test_stop_terminates(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.Popen", return_value=_FakeProc()):
            p.play("t.mp3")
            proc = p._proc
            p.stop()
        self.assertTrue(proc.terminated)
        self.assertFalse(p.is_playing())

    def test_set_volume_calls_pactl(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.run") as run:
            p.set_volume(150)  # clamped to 100
        self.assertEqual(
            run.call_args[0][0],
            ["pactl", "set-sink-volume", "sink0", "100%"],
        )

    def test_set_mute_calls_pactl(self):
        p = self._player()
        with mock.patch("utils.sound.subprocess.run") as run:
            p.set_mute(True)
        self.assertEqual(run.call_args[0][0], ["pactl", "set-sink-mute", "sink0", "1"])

    def test_missing_binary_disables_without_raising(self):
        p = self._player()
        with self.assertLogs("utils.sound", level="ERROR"), \
             mock.patch("utils.sound.subprocess.Popen", side_effect=FileNotFoundError()):
            p.play("t.mp3")          # must not raise
        with mock.patch("utils.sound.subprocess.Popen") as popen:
            p.play("t.mp3")          # disabled -> no further spawn
            popen.assert_not_called()

    def test_relative_sound_dir_resolved_against_base_dir(self):
        p = SoundPlayer(sink="", sound_dir="sounds", base_dir="/opt/adaptor")
        self.assertEqual(p.sound_dir, "/opt/adaptor/sounds")

    def test_disabled_player_is_noop(self):
        p = SoundPlayer(sink="", sound_dir="/snd", enabled=False)
        with mock.patch("utils.sound.subprocess.Popen") as popen:
            p.play("t.mp3")
            popen.assert_not_called()

    def test_missing_file_skips_without_spawn(self):
        p = self._player()
        with self.assertLogs("utils.sound", level="WARNING"), \
             mock.patch("utils.sound.os.path.exists", return_value=False), \
             mock.patch("utils.sound.subprocess.Popen") as popen:
            p.play("ghost.mp3")
            popen.assert_not_called()
        self.assertFalse(p.is_playing())

    def test_set_volume_empty_sink_uses_default_sink(self):
        p = SoundPlayer(sink="", sound_dir="/snd")
        with mock.patch("utils.sound.subprocess.run") as run:
            p.set_volume(50)
        self.assertEqual(run.call_args[0][0], ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "50%"])


if __name__ == "__main__":
    unittest.main()
