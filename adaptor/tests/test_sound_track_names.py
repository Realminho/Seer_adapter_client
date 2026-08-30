"""Cross-check shipped sound filenames against the names the adapter looks up.

Sound files are matched by naming CONVENTION -- `_resolve_sound_track` probes
`action-<actionType>.mp3` and `<state token>.mp3` and falls through to silence
when nothing matches. A typo, a rename, or a half-migrated file is therefore
invisible: no error, no log, just a robot that never makes a sound. The pin-map
tests (test_recipes_config / test_recipe_acceptance) guard the same class of
silent config mismatch; this guards the sound half of it.
"""

import re
from pathlib import Path

from adapter_jibot import Adapter
from config.config import get_config
from core.action_modules import first_party_action_specs
from core.factsheet import INSTANT_ACTION_TYPES


SOUND_DIR = Path(__file__).resolve().parents[1] / "sounds"

# _resolve_sound_track's non-action tiers, in the order it probes them:
# workingStateDetail, then the JIBOT avoidance tokens, then workingState.
# Keep in sync with _derive_amr_working_state / _jibot_avoidance_reason and the
# table in sounds/README.md.
STATE_TOKENS = {
    # workingStateDetail
    "emergency",
    "lost",
    "fault",
    "brake",
    "loading",
    "unloading",
    "docking",
    # JIBOT status avoidance tokens
    "slowdown",
    "watch",
    # workingState
    "error",
    "blocked",
    "paused",
    "charging",
    "driving",
    "acting",
    "idle",
    # _derive_jibot_stop_reason subtypes, which win over the detail file
    "manual",
    "protective_stop",
    "bumper",
    "motor_fault",
}

# Files kept in the tree that no lookup can reach. Listing one here is a
# deliberate "not wired up yet", not an exemption from the rule -- the second
# assertion below deletes the entry from under you once the file is gone, so
# the set cannot rot into a permanent allowlist.
UNREACHABLE = {
    "excuseme.mp3",   # no matching state token; predates the action- convention
    "moving.mp3",     # no MOVING working state exists; leftover of the action- rename
    "xuanzhuan.wav",  # .wav is only ever probed as ERROR<code>.wav, never by name
}

# 이름 규칙(_resolve_sound_track)이 아니라 코드가 직접 파일명을 지정해 재생하는
# 트랙들. 상수에서 가져오므로 이름을 바꾸면 여기도 같이 따라간다.
DIRECT_TRACKS = {
    Adapter.JOYSTICK_CONNECT_TRACK,   # play_joystick_connect_sound()
} | {
    # _play_mqtt_disconnect_sound(). 상수가 아니라 config 기본값이라 여기서 읽는다.
    # 빈 문자열은 "알림음 끔"이므로 배포 대상이 아니다.
    track
    for track in [(get_config().settings.mqtt_disconnect_sound or "").strip()]
    if track
}

ERROR_WAV = re.compile(r"^ERROR\d{4}\.wav$")
# Same sanitizer _resolve_sound_track applies before building the filename.
SANITIZE = re.compile(r"[^A-Za-z0-9_.-]+")


def _known_action_types() -> set[str]:
    config = get_config()
    types = {spec.action_type for spec in first_party_action_specs(config)}
    types.update(INSTANT_ACTION_TYPES)
    return {SANITIZE.sub("_", action_type).strip("._") for action_type in types}


def _sound_files() -> list[str]:
    return sorted(
        path.name
        for path in SOUND_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in (".mp3", ".wav")
    )


def test_action_sound_files_match_a_registered_action_type() -> None:
    """`action-<actionType>.mp3` must name an action that actually exists.

    Recipes are the trap: `pioElevatorOpen.mp3` looks right but the recipe is
    `pioElevatorOpen1f`, so the file never plays.
    """
    known = _known_action_types()
    unmatched = [
        name
        for name in _sound_files()
        if name.startswith("action-") and name[len("action-") : -len(".mp3")] not in known
    ]
    assert not unmatched, (
        f"sound files name no registered action type: {unmatched}. "
        "Expected action-<actionType>.mp3 with the actionType spelled exactly "
        "as declared in recipes.hcl / the extension module."
    )


def test_state_sound_files_match_a_token_the_resolver_probes() -> None:
    """Non-action files must be a state token, an ERROR wav, or declared unreachable."""
    stray = [
        name
        for name in _sound_files()
        if not name.startswith("action-")
        and not ERROR_WAV.match(name)
        and name not in UNREACHABLE
        and name not in DIRECT_TRACKS
        and Path(name).stem.lower() not in STATE_TOKENS
    ]
    assert not stray, (
        f"sound files match no state token the resolver probes: {stray}. "
        f"Rename to one of {sorted(STATE_TOKENS)}, to action-<actionType>.mp3, "
        "or add it to UNREACHABLE with the reason."
    )


def test_direct_tracks_are_shipped() -> None:
    """코드가 파일명을 박아 재생하는 트랙은 실제로 있어야 한다.

    없으면 has_track 가드에 걸려 조용히 넘어가므로, 오타나 누락이 무증상이 된다.
    """
    missing = sorted(DIRECT_TRACKS - set(_sound_files()))
    assert not missing, (
        f"code plays these by name but they are not under sounds/: {missing}"
    )


def test_unreachable_list_has_no_stale_entries() -> None:
    """Deleting a dead file must also delete its excuse."""
    present = set(_sound_files())
    stale = sorted(UNREACHABLE - present)
    assert not stale, f"UNREACHABLE lists files that no longer exist: {stale}"
