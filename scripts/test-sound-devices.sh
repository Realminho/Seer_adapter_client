#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PLAYER="${PLAYER:-mplayer}"
ALSA_CARD="${ALSA_CARD:-0}"
SECONDS_PER_SINK=3
TRACK=""
LIST_ONLY=0
PREPARE_RT5651=0
SHOW_MIXER=0
DIRECT_ALSA=0
INTERACTIVE=0
SCAN_QUIT=0
AUDIBLE_PULSE_SINKS=()
AUDIBLE_ALSA_DEVICES=()

RT5651_ROUTE_SWITCHES=(
  "Headphone"
  "Speaker"
  "HPO L"
  "HPO R"
  "HPO MIX DAC1"
  "HPO MIX HPVOL"
  "HPOVOL L"
  "HPOVOL R"
  "OUT MIXL DAC L1"
  "OUT MIXR DAC R1"
  "OUTVOL L"
  "OUTVOL R"
  "LOUT L"
  "LOUT R"
  "LOUT MIX DAC L1"
  "LOUT MIX DAC R1"
  "LOUT MIX OUTVOL L"
  "LOUT MIX OUTVOL R"
  "Stereo DAC MIXL DAC L1"
  "Stereo DAC MIXR DAC R1"
  "DAC2"
)

RT5651_VOLUME_CONTROLS=(
  "HP"
  "OUT"
  "DAC1"
  "Mono DAC"
)

usage() {
  cat <<'EOF'
Usage: scripts/test-sound-devices.sh [--track PATH] [--seconds N] [--list-only]
                                    [--prepare-rt5651] [--show-mixer]
                                    [--direct-alsa] [--interactive]

Play the same test sound through every PulseAudio sink so the audible sink can
be copied into config/config.toml:

  [sound_settings]
  sink = "<audible sink name>"

Options:
  --track PATH    mp3/wav file to play. Default: sounds/driving.mp3 if present.
  --seconds N     seconds to play per sink. Default: 3.
  --list-only     only list PulseAudio sinks.
  --prepare-rt5651
                  enable the known rt5651 codec output route switches.
  --show-mixer    print focused rt5651 mixer controls.
  --direct-alsa   play a 1kHz tone directly to plughw:<ALSA_CARD>,0.
  --interactive   ask which PulseAudio and ALSA outputs are audible.

Environment:
  PLAYER          playback binary. Default: mplayer.
  ALSA_CARD       ALSA card index for rt5651 mixer/direct ALSA. Default: 0.
EOF
}

show_rt5651_mixer() {
  echo "Focused rt5651 mixer controls (ALSA card $ALSA_CARD):"
  for control in "${RT5651_ROUTE_SWITCHES[@]}" "${RT5651_VOLUME_CONTROLS[@]}"; do
    echo "-- $control"
    amixer -c "$ALSA_CARD" get "$control" 2>/dev/null | sed -n '1,8p' || true
  done
}

prepare_rt5651_routes() {
  echo "Enabling rt5651 output route switches (ALSA card $ALSA_CARD):"
  for control in "${RT5651_ROUTE_SWITCHES[@]}"; do
    echo "  $control -> on"
    amixer -c "$ALSA_CARD" set "$control" on >/dev/null 2>&1 || true
  done
  for control in "${RT5651_VOLUME_CONTROLS[@]}"; do
    echo "  $control -> 90%"
    amixer -c "$ALSA_CARD" set "$control" 90% >/dev/null 2>&1 || true
  done
}

direct_alsa_test() {
  if ! command -v speaker-test >/dev/null 2>&1; then
    echo "ERROR: speaker-test not found" >&2
    exit 1
  fi
  echo "Playing direct ALSA test tone on plughw:${ALSA_CARD},0 for ${SECONDS_PER_SINK}s."
  if command -v pasuspender >/dev/null 2>&1; then
    timeout "${SECONDS_PER_SINK}s" \
      pasuspender -- speaker-test -D "plughw:${ALSA_CARD},0" -t sine -f 1000 -c 2 -l 1
  else
    timeout "${SECONDS_PER_SINK}s" \
      speaker-test -D "plughw:${ALSA_CARD},0" -t sine -f 1000 -c 2 -l 1
  fi
}

ask_audible() {
  local answer
  while true; do
    printf '    Did you hear it? [y]es/[N]o/[r]eplay/[q]uit: ' >&3
    if ! IFS= read -r -u 3 answer; then
      echo >&3
      SCAN_QUIT=1
      return 3
    fi
    answer="${answer,,}"
    case "$answer" in
      y|yes)
        return 0
        ;;
      ""|n|no)
        return 1
        ;;
      r|replay)
        return 2
        ;;
      q|quit)
        SCAN_QUIT=1
        return 3
        ;;
      *)
        echo "    Enter y, n, r, or q." >&3
        ;;
    esac
  done
}

play_pulse_sink() {
  local sink_index="$1"
  local sink_name="$2"
  local sink_state="$3"
  local answer_status
  local playback_status

  while true; do
    echo "==> [$sink_index] $sink_name (state=${sink_state:-unknown})"
    playback_status=0
    PULSE_SINK="$sink_name" timeout "${SECONDS_PER_SINK}s" \
      "$PLAYER" -really-quiet -noconsolecontrols -ao "pulse::${sink_name}" "$TRACK" \
      </dev/null >/dev/null 2>&1 || playback_status=$?
    if [[ "$playback_status" -ne 0 && "$playback_status" -ne 124 ]]; then
      echo "    Playback command failed (exit $playback_status)."
    fi
    if ask_audible; then
      AUDIBLE_PULSE_SINKS+=("$sink_name")
      return 0
    else
      answer_status=$?
    fi
    case "$answer_status" in
      1) return 0 ;;
      2) continue ;;
      3) return 0 ;;
    esac
  done
}

discover_alsa_devices() {
  if ! command -v aplay >/dev/null 2>&1; then
    echo "SKIP: aplay not found; direct ALSA outputs cannot be listed." >&2
    return 0
  fi
  aplay -l 2>/dev/null \
    | sed -nE 's/^card ([0-9]+):.*, device ([0-9]+):.*/plughw:\1,\2/p' \
    | awk '!seen[$0]++'
}

play_alsa_device() {
  local device="$1"
  local answer_status
  local playback_status

  while true; do
    echo "==> ALSA $device"
    playback_status=0
    if command -v pasuspender >/dev/null 2>&1; then
      timeout "${SECONDS_PER_SINK}s" \
        pasuspender -- speaker-test -D "$device" -t sine -f 1000 -c 2 -l 1 \
        </dev/null >/dev/null 2>&1 || playback_status=$?
    else
      timeout "${SECONDS_PER_SINK}s" \
        speaker-test -D "$device" -t sine -f 1000 -c 2 -l 1 \
        </dev/null >/dev/null 2>&1 || playback_status=$?
    fi
    if [[ "$playback_status" -ne 0 && "$playback_status" -ne 124 ]]; then
      echo "    Playback command failed (exit $playback_status)."
    fi
    if ask_audible; then
      AUDIBLE_ALSA_DEVICES+=("$device")
      return 0
    else
      answer_status=$?
    fi
    case "$answer_status" in
      1) return 0 ;;
      2) continue ;;
      3) return 0 ;;
    esac
  done
}

print_interactive_summary() {
  echo
  echo "Sound output scan summary"
  if [[ "${#AUDIBLE_PULSE_SINKS[@]}" -gt 0 ]]; then
    echo "Confirmed audible PulseAudio sinks:"
    for sink_name in "${AUDIBLE_PULSE_SINKS[@]}"; do
      echo "  sink = \"$sink_name\""
    done
  fi
  if [[ "${#AUDIBLE_ALSA_DEVICES[@]}" -gt 0 ]]; then
    echo "Confirmed audible ALSA devices:"
    for device in "${AUDIBLE_ALSA_DEVICES[@]}"; do
      echo "  $device"
    done
    if [[ "${#AUDIBLE_PULSE_SINKS[@]}" -eq 0 ]]; then
      echo "Hardware playback works, but its PulseAudio sink must be restored or created."
    fi
  fi
  if [[ "$SCAN_QUIT" -eq 1 ]]; then
    echo "Partial scan: operator quit before all outputs were tested."
    return 130
  fi
  if [[ "${#AUDIBLE_PULSE_SINKS[@]}" -gt 0 || "${#AUDIBLE_ALSA_DEVICES[@]}" -gt 0 ]]; then
    return 0
  fi
  echo "No audible output was confirmed. Check codec enumeration, amplifier power, mixer routes, and wiring."
  return 1
}

show_configured_sink() {
  local config_file
  local configured_sink
  for config_file in \
    "$PWD/config/config.toml" \
    "$REPO_ROOT/adaptor/config/config.toml" \
    "$REPO_ROOT/config/config.toml"; do
    [[ -r "$config_file" ]] || continue
    configured_sink="$(awk '
      /^\[sound_settings\][[:space:]]*(#.*)?$/ { in_sound = 1; next }
      /^\[/ { in_sound = 0 }
      in_sound && /^[[:space:]]*sink[[:space:]]*=/ {
        value = $0
        sub(/^[^=]*=[[:space:]]*/, "", value)
        sub(/[[:space:]]*#.*/, "", value)
        gsub(/^"|"$/, "", value)
        print value
        exit
      }
    ' "$config_file")"
    if [[ -n "$configured_sink" ]]; then
      echo "Configured sink: $configured_sink"
      return 0
    fi
  done
  echo "Configured sink: not found"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --track)
      TRACK="${2:-}"
      shift 2
      ;;
    --seconds)
      SECONDS_PER_SINK="${2:-}"
      shift 2
      ;;
    --list-only)
      LIST_ONLY=1
      shift
      ;;
    --prepare-rt5651)
      PREPARE_RT5651=1
      shift
      ;;
    --show-mixer)
      SHOW_MIXER=1
      shift
      ;;
    --direct-alsa)
      DIRECT_ALSA=1
      shift
      ;;
    --interactive)
      INTERACTIVE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$INTERACTIVE" -eq 1 ]]; then
  if ! exec 3<>/dev/tty; then
    echo "ERROR: --interactive requires a controlling terminal" >&2
    exit 2
  fi
fi

if ! [[ "$SECONDS_PER_SINK" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "ERROR: --seconds must be a positive number" >&2
  exit 2
fi

if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
  XDG_RUNTIME_DIR="/run/user/$(id -u)"
  export XDG_RUNTIME_DIR
fi

if [[ "$PREPARE_RT5651" -eq 1 ]]; then
  prepare_rt5651_routes
fi

if [[ "$SHOW_MIXER" -eq 1 ]]; then
  show_rt5651_mixer
fi

if [[ "$DIRECT_ALSA" -eq 1 ]]; then
  direct_alsa_test || true
  exit 0
fi

if [[ -z "$TRACK" ]]; then
  for candidate in \
    "$REPO_ROOT/sounds/driving.mp3" \
    "$REPO_ROOT/adaptor/sounds/driving.mp3" \
    "$REPO_ROOT/sounds/work.mp3" \
    "$REPO_ROOT/adaptor/sounds/work.mp3"; do
    if [[ -f "$candidate" ]]; then
      TRACK="$candidate"
      break
    fi
  done
fi

if [[ "$LIST_ONLY" -eq 0 && ! -f "$TRACK" ]]; then
  echo "ERROR: test sound file not found; pass --track /path/to/file.mp3" >&2
  exit 1
fi

if [[ "$INTERACTIVE" -eq 1 ]]; then
  show_configured_sink
fi

PULSE_AVAILABLE=1
if ! command -v pactl >/dev/null 2>&1; then
  if [[ "$INTERACTIVE" -eq 1 ]]; then
    echo "SKIP: pactl not found; continuing with direct ALSA outputs." >&2
    PULSE_AVAILABLE=0
  else
    echo "ERROR: pactl not found" >&2
    exit 1
  fi
fi

if [[ "$LIST_ONLY" -eq 0 && "$PULSE_AVAILABLE" -eq 1 ]]; then
  if ! command -v "$PLAYER" >/dev/null 2>&1; then
    echo "ERROR: player not found: $PLAYER" >&2
    exit 1
  fi
fi

sinks=""
if [[ "$PULSE_AVAILABLE" -eq 1 ]]; then
  sinks="$(pactl list short sinks)"
fi
if [[ -z "$sinks" && "$INTERACTIVE" -eq 0 ]]; then
  echo "ERROR: no PulseAudio sinks found" >&2
  exit 1
fi

if [[ -n "$sinks" ]]; then
  echo "Available PulseAudio sinks:"
  printf '%s\n' "$sinks" | awk -F '\t' '{printf "  [%s] %s  state=%s\n", $1, $2, $5}'
else
  echo "No PulseAudio sinks available; continuing with direct ALSA outputs."
fi

if [[ "$LIST_ONLY" -eq 1 ]]; then
  exit 0
fi

echo
echo "Test track: $TRACK"
echo "Playing each sink for ${SECONDS_PER_SINK}s."
echo "When you hear sound, copy this into config/config.toml:"
echo '  sink = "<sink name>"'
echo

if [[ "$INTERACTIVE" -eq 1 ]]; then
  while IFS=$'\t' read -r sink_index sink_name_raw _driver _sample sink_state _rest; do
    sink_name="$sink_name_raw"
    [[ -n "${sink_name:-}" ]] || continue
    play_pulse_sink "$sink_index" "$sink_name" "$sink_state"
    if [[ "$SCAN_QUIT" -eq 1 ]]; then
      break
    fi
  done <<< "$sinks"
  if [[ "$SCAN_QUIT" -eq 0 ]]; then
    if command -v speaker-test >/dev/null 2>&1; then
      while IFS= read -r alsa_device; do
        [[ -n "$alsa_device" ]] || continue
        play_alsa_device "$alsa_device"
        if [[ "$SCAN_QUIT" -eq 1 ]]; then
          break
        fi
      done < <(discover_alsa_devices)
    else
      echo "SKIP: speaker-test not found; direct ALSA outputs cannot be tested." >&2
    fi
  fi
  if print_interactive_summary; then
    exit 0
  else
    exit $?
  fi
fi

printf '%s\n' "$sinks" | while IFS=$'\t' read -r sink_index sink_name_raw _driver _sample sink_state _rest; do
  sink_name="$sink_name_raw"
  [[ -n "${sink_name:-}" ]] || continue
  echo "==> [$sink_index] $sink_name (state=${sink_state:-unknown})"
  echo "    config/config.toml: sink = \"$sink_name\""
  PULSE_SINK="$sink_name" timeout "${SECONDS_PER_SINK}s" \
    "$PLAYER" -really-quiet -noconsolecontrols -ao "pulse::${sink_name}" "$TRACK" \
    </dev/null >/dev/null 2>&1 || true
  sleep 0.5
done
