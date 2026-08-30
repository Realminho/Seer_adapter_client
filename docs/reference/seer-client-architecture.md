# SEER client groundwork — architecture

Status: **partially implemented** (skeleton 2026-06-26 → slices 1–4 implemented via
TDD). Interfaces/classes/markers complete; the transport + core commands are real,
tested code; some commands remain `NotImplementedError` stubs pending SEER spec.

SEER 클라이언트 — 아키텍처. 상태: **부분 구현**(초석 후 슬라이스 1–4를 TDD로 구현).
인터페이스·클래스·마커 완성; 전송+핵심 명령은 실제 테스트된 코드; 일부 명령은 SEER
스펙 확인 전까지 `NotImplementedError` 스텁.

> **다음 세션 시작점 / start here next session**: 아래 "Current status" + "Open
> question: SEER API" 를 먼저 읽을 것. 구현 현황 매트릭스는 `seer-client/README.md`.

## What was added / 추가된 것

Two new packages, plus zero changes to `jibot-client`:

- **`amr-client-contract/`** — the common adaptor↔client contract. Interfaces only,
  zero runtime deps. Protocols (`AmrConnection`, `AmrMotion`, `AmrStateReader`,
  `AmrTelemetryInjection`, `AmrClient`) + marker interfaces (`SupportsDocking`, …).
- **`seer-client/`** — `SeerClient`, an async skeleton implementing that contract
  for SEER (Robokit) AMRs, mirroring `jibot-client`'s structure.

## Relationship / 관계

```
        adaptor  ───►  amr-client-contract  ◄───  seer-client (SeerClient)
        vehicle:            (Protocols +              implements directly,
        AmrClient            markers, deps 0)         inherits markers
                                   ▲
                                   └──── jibot-client (JIBOT)
                                         structural & PARTIAL — unchanged
```

- Contract depends on nothing → no cycles, **clients never depend on the adaptor**.
- `JIBOT` conforms **structurally** (it is never edited). It is a **partial** match:
  public wrappers + injectors already line up; the clean names `dock`/`localize`/
  `drive` and the state *properties* (`x`,`y`,…) are not on JIBOT yet (it has
  `um_dock`/… and private `_x`/…). Full nominal conformance is a future,
  **purely-additive** shim on jibot-client — no runtime change, out of scope now.
- `SeerClient` is built clean-name-first, so it satisfies the Protocols directly
  and inherits the capability markers.

## Two interface kinds / 두 인터페이스 종류

| Kind | Built with | Satisfied | Examples |
|---|---|---|---|
| Interface | `typing.Protocol` | structurally (name match) | `AmrMotion`, `AmrStateReader` |
| Marker interface | empty `abc.ABC` | nominally (explicit inherit) | `SupportsDocking`, `SupportsRelocation` |

## Member marking / 멤버 마킹

Within `SeerClient` every member is tagged:

- `[ADAPTOR-CONTRACT]` — the adaptor calls/reads it today (via JIBOT) → implement first.
- `[SEER-EXT]` — SEER-specific / future, outside the common contract.
- `[SEER-INTERNAL]` — plumbing (dispatch, poll loop, parse).

See the implementation-status matrix in `seer-client/README.md`.

## SEER vs JIBOT transport / 전송 차이

| | JIBOT | SEER |
|---|---|---|
| Ports | 1 (TCP 7273) | several (19204 state / 19205 control / 19206 task / 19210 IO …) |
| Frame | text `$#<len>##<json>$~` | binary 16-byte header (sync `0x5A`) + JSON body |
| Status | passive: parse a free stream | active: poll the STATE port (`_poll_loop`) |

This is why `seer-client` adds `connection.SeerPortConnection` (one socket per
port) and `protocol.pack_message`/`unpack_header`, where JIBOT needs only a single
`reader`/`writer` and an inline `parse_string`.

## Out of scope (next steps) / 범위 밖(다음 단계)

1. **Fill the stubs** below — needs SEER API confirmation (see Open question).
2. `adapter_seer.py` + `main.py` wiring (annotate `vehicle: AmrClient`); large,
   separate effort akin to `adapter_jibot.py`.
3. Optional: make `jibot-client` *nominally* conform (additive aliases/properties)
   and add a `SimulatedSeer` subclass (mirrors `SimulatedJIBOT`).
4. Add a type-checker (mypy/pyright) pass in CI to enforce the
   `_assert_conforms` guard in `seer_client/client.py`.

## Current status (slices 1–4, TDD) / 현재 상태

Committed and green: `adaptor/tests/test_seer_client_*.py` → **40 passed**. All 19
new files are tracked in git.

**✅ Implemented + tested (real code, not skeleton):**
- `protocol.pack_message` / `unpack_header` — 16-byte frame codec (sync `0x5A`,
  compact JSON, byte-accurate length, desync `ValueError`).
- `connection.SeerPortConnection` — per-port async socket; `connect`/`disconnect`/
  `request` (pack→send→recv-header→recv-body→json, `msg_typ`-echo desync check),
  `_next_req_id` (uint16 wrap).
- `SeerClient`: `connect_socket`/`disconnect`/`reconnect` (N-port pool + poll task),
  `send_command`/`validate_command_params` (COMMAND_CATALOG dispatch),
  `_poll_once`/`_poll_loop`/`_process_status` (active STATE polling → state cache),
  motion `goto_point`/`goto_xyz`/`drive`/`localize`, `set_do`, and the injection +
  connection-status members (`set_bms`, `is_rx_stale`, …).

**⬜ Still `NotImplementedError` stubs (no repo source → not guessed):**
- Contract motion: `stop_motion` (cancel/pause API# — absent from vendor enum),
  `enable_motor`/`disable_motor` (open/close-loop API#), `dock` (mechanics),
  `move_distance`.
- SEER-EXT queries: `get_battery_info` / `get_task_status` / `get_blocked` /
  `get_map_info` / `map_switch` / `relocation` / `free_nav` / `read_di`
  (these are one-liners over `send_command` once confirmed).
- State fields left at defaults: `status`, `mode`, `station`, `motor_flag`,
  `localization_score` — SEER field names unconfirmed (`current_station`,
  `confidence`?). Getters are live; the poll-loop fill is TODO.

**Provisional (grounded in vendor demo code, not the spec):** `battery_level`×100
→ percent (0..1 assumed); `drive` trans/rot/speed/lat → vx/vy/w/duration pulse.

## Open question: SEER API / 미확인: SEER API

Implemented mappings come from **in-repo vendor SDK code** (verified):
`seer/SamDisplay/Robokit_TCP_API_py/netprotocol/rbkNetProtoEnums.py` (ports, API
numbers, `!BBHLH6s` frame) and `seer/SamDisplay/custom_package/seer_commu.py` +
`gotarget.py` + `rbkApiSetDO.py` (field names, body shapes). The vendor enum has
only: reloc 2002, motion 2010, loadmap 2022, gotarget 3051, setdo 6001 (+ status
1000-range) — which is exactly why the stubs above are stubbed.

**Not yet read:** the authoritative spec
`seer/SamDisplay/Robokit_TCP_API_py/robotkit-netprotocol-l-1.2.1.pdf` (1 MB,
`pdftotext`-able). Reading it is the prerequisite to (a) verify the provisional
mappings and (b) fill the stubs with confirmed API numbers/fields. Do NOT rely on
model training memory of SEER — firmware/protocol versions differ.

## Verify / 검증

```bash
PYTHONPATH=amr-client-contract/src:seer-client/src python3 -c "
from seer_client import SeerClient
from amr_client_contract import AmrClient
assert isinstance(SeerClient('1.2.3.4'), AmrClient)
print('SeerClient conforms to AmrClient')"
```
