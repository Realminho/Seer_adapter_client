import errno
import os
import pty
import select
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOUND_TEST_SCRIPT = REPO_ROOT / "scripts" / "test-sound-devices.sh"
TEST_TRACK = REPO_ROOT / "adaptor" / "sounds" / "driving.mp3"


@dataclass
class ScriptResult:
    returncode: int
    output: str


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run_interactive_probe(
    tmp_path: Path,
    *,
    answers: str,
    pactl_output: str,
    aplay_output: str = "",
    mplayer_exit: int = 0,
) -> tuple[ScriptResult, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    playback_log = tmp_path / "playback.log"

    _write_executable(
        fake_bin / "pactl",
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == \"list short sinks\" ]]; then\n"
        "  printf '%b' \"$FAKE_PACTL_OUTPUT\"\n"
        "fi\n",
    )
    _write_executable(
        fake_bin / "mplayer",
        "#!/usr/bin/env bash\n"
        "printf 'pulse:%s\\n' \"${PULSE_SINK:-}\" >> \"$PLAYBACK_LOG\"\n"
        "exit \"$FAKE_MPLAYER_EXIT\"\n",
    )
    _write_executable(
        fake_bin / "aplay",
        "#!/usr/bin/env bash\n"
        "printf '%b' \"$FAKE_APLAY_OUTPUT\"\n",
    )
    _write_executable(
        fake_bin / "speaker-test",
        "#!/usr/bin/env bash\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  if [[ \"$1\" == \"-D\" ]]; then\n"
        "    printf 'alsa:%s\\n' \"$2\" >> \"$PLAYBACK_LOG\"\n"
        "    exit 0\n"
        "  fi\n"
        "  shift\n"
        "done\n"
        "exit 2\n",
    )
    _write_executable(
        fake_bin / "pasuspender",
        "#!/usr/bin/env bash\n"
        "[[ \"${1:-}\" == \"--\" ]] && shift\n"
        "exec \"$@\"\n",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["PLAYBACK_LOG"] = str(playback_log)
    env["FAKE_PACTL_OUTPUT"] = pactl_output
    env["FAKE_APLAY_OUTPUT"] = aplay_output
    env["FAKE_MPLAYER_EXIT"] = str(mplayer_exit)

    pid, master_fd = pty.fork()
    if pid == 0:
        os.chdir(REPO_ROOT)
        os.execvpe(
            "bash",
            [
                "bash",
                str(SOUND_TEST_SCRIPT),
                "--interactive",
                "--track",
                str(TEST_TRACK),
                "--seconds",
                "0.1",
            ],
            env,
        )

    os.write(master_fd, answers.encode())
    chunks: list[bytes] = []
    deadline = time.monotonic() + 10
    status = None
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master_fd], [], [], 0.1)
            if readable:
                try:
                    chunks.append(os.read(master_fd, 65536))
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
            waited_pid, waited_status = os.waitpid(pid, os.WNOHANG)
            if waited_pid == pid:
                status = waited_status
                break
        if status is None:
            os.kill(pid, signal.SIGKILL)
            _, status = os.waitpid(pid, 0)
            raise AssertionError("interactive sound probe timed out")
    finally:
        os.close(master_fd)

    returncode = os.waitstatus_to_exitcode(status)
    return ScriptResult(returncode, b"".join(chunks).decode(errors="replace")), playback_log


def test_interactive_replays_and_records_audible_pulse_sink(tmp_path: Path) -> None:
    result, playback_log = _run_interactive_probe(
        tmp_path,
        answers="n\nr\ny\n",
        pactl_output=(
            "1\\tsink-one\\tmodule-alsa-card.c\\ts16le 2ch 44100Hz\\tSUSPENDED\\n"
            "2\\tsink-two\\tmodule-alsa-card.c\\ts16le 2ch 44100Hz\\tSUSPENDED\\n"
        ),
    )

    assert result.returncode == 0
    assert playback_log.read_text().splitlines() == [
        "pulse:sink-one",
        "pulse:sink-two",
        "pulse:sink-two",
    ]
    assert 'sink = "sink-two"' in result.output
    assert "Confirmed audible PulseAudio sinks" in result.output
    assert "Configured sink: alsa_output.platform-rt5651-sound.stereo-fallback" in result.output


def test_interactive_quit_stops_before_remaining_outputs(tmp_path: Path) -> None:
    result, playback_log = _run_interactive_probe(
        tmp_path,
        answers="q\n",
        pactl_output=(
            "1\\tsink-one\\tmodule-alsa-card.c\\ts16le 2ch 44100Hz\\tSUSPENDED\\n"
            "2\\tsink-two\\tmodule-alsa-card.c\\ts16le 2ch 44100Hz\\tSUSPENDED\\n"
        ),
    )

    assert result.returncode == 130
    assert playback_log.read_text().splitlines() == ["pulse:sink-one"]
    assert "Partial scan" in result.output


def test_interactive_requires_controlling_terminal() -> None:
    result = subprocess.run(
        [
            "bash",
            str(SOUND_TEST_SCRIPT),
            "--interactive",
            "--track",
            str(TEST_TRACK),
        ],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        start_new_session=True,
    )

    assert result.returncode == 2
    assert "requires a controlling terminal" in result.stdout


def test_interactive_returns_one_when_no_output_is_audible(tmp_path: Path) -> None:
    result, playback_log = _run_interactive_probe(
        tmp_path,
        answers="n\nn\n",
        pactl_output=(
            "1\\tsink-one\\tmodule-alsa-card.c\\ts16le 2ch 44100Hz\\tSUSPENDED\\n"
            "2\\tsink-two\\tmodule-alsa-card.c\\ts16le 2ch 44100Hz\\tSUSPENDED\\n"
        ),
    )

    assert result.returncode == 1
    assert playback_log.read_text().splitlines() == ["pulse:sink-one", "pulse:sink-two"]
    assert "No audible output was confirmed" in result.output


def test_interactive_classifies_alsa_only_success(tmp_path: Path) -> None:
    result, playback_log = _run_interactive_probe(
        tmp_path,
        answers="n\nn\ny\n",
        pactl_output=(
            "1\\tsink-one\\tmodule-alsa-card.c\\ts16le 2ch 44100Hz\\tSUSPENDED\\n"
        ),
        aplay_output=(
            "**** List of PLAYBACK Hardware Devices ****\\n"
            "card 0: hdmisound [hdmi-sound], device 0: i2s-hifi [i2s-hifi]\\n"
            "card 2: rt5651 [realtek,rt5651-codec], device 0: fe410000.i2s [rt5651-aif1]\\n"
        ),
    )

    assert result.returncode == 0
    assert playback_log.read_text().splitlines() == [
        "pulse:sink-one",
        "alsa:plughw:0,0",
        "alsa:plughw:2,0",
    ]
    assert "Hardware playback works, but its PulseAudio sink must be restored" in result.output
    assert "plughw:2,0" in result.output


def test_interactive_does_not_report_expected_timeout_as_playback_failure(
    tmp_path: Path,
) -> None:
    result, _ = _run_interactive_probe(
        tmp_path,
        answers="y\n",
        pactl_output=(
            "1\\tsink-one\\tmodule-alsa-card.c\\ts16le 2ch 44100Hz\\tSUSPENDED\\n"
        ),
        mplayer_exit=124,
    )

    assert result.returncode == 0
    assert "Playback command returned non-zero" not in result.output
