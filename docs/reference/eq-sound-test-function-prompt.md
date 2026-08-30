# Handoff prompt — AMR v3 eq sound-test function

Paste the block below to an agent/developer working in the **AMR v3 eq repo
(`fabris-equipments`, branch `develop`)**. It asks them to build a function that
exercises the JIBOT adapter's new sound instant actions over the existing
VDA5050/MQTT path.

---

You are adding a **sound-test helper** to the AMR v3 eq (`fabris-equipments`).
The JIBOT adapter (`unified-amr-adaptor`) now supports OS-level sound, controlled
entirely through VDA5050 **instant actions** that the eq publishes to the AMR
(same MQTT instant-actions topic the eq already uses for orders/instant actions).
Build a function the operator can call to test that sound works end-to-end.

## Background / cross-repo contract

- The adapter plays audio on the **adapter host speaker** (not the robot). The eq
  cannot "hear" it; it can confirm the action was **delivered and reported
  FINISHED**, and the operator confirms audio physically.
- The adapter already publishes `information[]` with `infoType="AMR_STATE"`
  (`workingState`/`workingStateDetail`) which the eq consumes in
  `packages/amr/src/lib/amr/amr.types.ts` (`toAmrWorkingState`/
  `toAmrWorkingStateDetail`, `decideState()`). Sound is driven by that same
  `workingState` on the adapter side — your test function does NOT need to set it;
  it only needs to trigger the manual sound actions below.
- The adapter advertises these action types in its **factsheet**
  (`protocolFeatures.agvActions`, `actionScopes: ["INSTANT"]`). Optionally read
  the retained factsheet first as a capability gate before sending.

## The four sound instant actions (what the adapter accepts)

Each is a VDA5050 instant action: `{ actionType, actionId, blockingType: "NONE",
actionParameters: [{ key, value }] }`. The adapter sets the matching
`instantActionStates[]` entry to `RUNNING` then `FINISHED` (or `FAILED` with a
`resultDescription`); terminal entries are cleared from the next state message.

| actionType | actionParameters | effect | success |
|---|---|---|---|
| `testSound` | `sound` = `"travel"` \| `"work"` \| an mp3 filename (default `"travel"`); `seconds` = override duration, default `10` | plays the track on the host speaker for `seconds`, then the adapter auto-resumes its workingState-driven audio | `FINISHED`, desc `"testing <file> for <n>s"` |
| `stopSound` | (none) | stops test playback now, returns control to the auto-driver | `FINISHED` |
| `setSoundVolume` | `volume` = `0`–`100`; optional `mute` = `"true"`/`"false"` | sets the host OS sink volume / mute | `FINISHED` |
| `uploadSound` | `fileName` = a `*.mp3` basename; `data` = base64 of the mp3 bytes; optional `encoding` = `"base64"` | stores the mp3 in the adapter's sound dir (e.g. `travel.mp3`/`work.mp3`) | `FINISHED`, desc `"stored <file> (<n> bytes)"` |

Failure modes to surface as `FAILED` (read `resultDescription`): non-`.mp3`
fileName, invalid/empty base64, write error, bad volume.

## What to build

A function (and a thin CLI/route or test entry if that matches eq conventions),
e.g. `sendSoundTest(amrId, { action, params })`, that:

1. Builds a VDA5050 instant-action message for the AMR (reuse the eq's existing
   instant-action builder/publisher — find where the eq currently emits instant
   actions to an AMR and use that exact path; do not invent a new MQTT client).
2. Sends one of: `testSound` (default to play `travel` for ~5s), `stopSound`,
   `setSoundVolume`, `uploadSound`.
3. Awaits/observes the AMR's next state messages and reports the matching
   `instantActionStates` outcome (`FINISHED`/`FAILED` + `resultDescription`),
   with a timeout. Delivered-but-no-terminal-within-timeout = report as unknown.
4. For `uploadSound`, accepts a local mp3 path, base64-encodes it, and sends it;
   warn if the file is large (it travels in one MQTT message).

Provide a minimal operator entry point: "play travel test sound on AMR X",
"set volume 60 on AMR X", "upload ./travel.mp3 to AMR X".

## Verify

- Unit-test the message construction (correct actionType + actionParameters
  key/value pairs) with the eq's existing test tooling — follow the repo's
  patterns (TDD if the repo does).
- Manual acceptance on a real/sim AMR:
  - `testSound travel` → operator hears travel music for the duration, action
    `FINISHED`, then audio returns to whatever workingState dictates.
  - `setSoundVolume 30` then `testSound` → quieter; `setSoundVolume 90` → louder.
  - `uploadSound` a small mp3 named `travel.mp3` → `FINISHED`; a later
    `testSound travel` plays the new file.
  - `stopSound` during a long `testSound` → audio stops immediately.

## Constraints

- Reuse the eq's existing instant-action publish path and AMR addressing; do not
  add a parallel MQTT client.
- Keep action parameter keys/values EXACTLY as in the table (the adapter matches
  them verbatim).
- Don't change the AMR_STATE consumption code; sound is independent of it.
- Follow the eq repo's module structure, naming, and test conventions.

Deliverable: the function + tests + a one-paragraph note on how an operator
invokes it.
