# 연속 경로 주행 — SDD 실행 원장 (보존본)

> 원본은 `.superpowers/sdd/2026-08-27-continuous-path/progress.md` 였고 gitignore 대상이라
> 실행 종료 시 삭제된다. 판단 19건과 그 근거·비용이 여기에만 남으므로 복사해 둔다.
> 커밋 범위: 37e2327..3b6966e (11커밋, origin/develop 에 푸시됨 2026-08-27)

# SDD ledger — plan: docs/superpowers/plans/2026-08-27-continuous-path.md

Spec: docs/superpowers/specs/2026-08-26-continuous-path-design.md (읽음)
Workspace: .superpowers/sdd/2026-08-27-continuous-path/
Branch: develop (메인 브랜치 직접 작업 — 사용자 명시 승인 2026-08-27)

## 작업 공간 결정

다른 세션이 미커밋으로 편집 중인 파일이 우리 대상과 4개 겹친다:
adapter_jibot.py(+581), config.py(+36), config.toml(+21),
test_adapter_jibot_v3_order.py(+40). plan/spec 의 줄번호는 **이 미커밋 상태 기준**으로
검증된 것이라 HEAD 워크트리로 가면 전부 어긋난다.

Ruling: develop 워킹트리에서 직접 작업한다 — 사용자가 세 선택지를 보고 명시 승인했다.
비용: 다른 세션이 같은 파일을 동시에 편집하면 덮어쓰기가 날 수 있다. 각 태스크가
커밋으로 끝나므로 복구는 git log 로 가능하다.

## 사전 충돌 스캔

### 파일/인터페이스를 공유하는 태스크 쌍

| 쌍 | 무엇을 주고받나 | 결과 |
|---|---|---|
| T1 → T2 | T2 Consumes 에 `FakeVehicle.start_driving()` 이라 적혀 있으나 T2 테스트는 쓰지 않는다 | 무해. Consumes 과다 선언. 조치 없음 |
| T1 → T5 | `start_driving()` — T5 테스트가 "주행 중" 상태를 만들 때 쓴다 | 일치 |
| T2 → T5 | `_clear_edge_states_up_to()` — T2 가 `_finalize_v3_node_step` 에 이미 배선하므로 T5 는 간접 소비 | 일치(간접). T5 가 직접 부르지 않아도 된다 |
| T3 → T5 | `_continuous_path_enabled()` — T5 `_should_settle_at`/`_is_run_end` 가 호출 | 일치 |
| T3 → T6 | `_continuous_path_enabled()` — T6 `_set_new_base_request_from_queue` 가 호출 | 일치 |
| **T4 → T5** | **`segment_passes_within()` — T5 Consumes 에 적혀 있으나 T5 코드가 아무 데서도 호출하지 않는다** | **결함. 아래 Ruling 1** |
| T5 → T6 | T6 Consumes 에 `drivable_run` 이라 적혀 있으나 T6 코드는 `order_queue._queue` 를 직접 센다 | 무해. Consumes 과다 선언. 조치 없음 |
| T2·T3·T5·T6 | 전부 `adapter_jibot.py` 를 수정한다. 서로 다른 메서드를 추가하고 겹치는 편집 지점은 `_finalize_v3_node_step`(T2 가 엣지 정리, T6 이 base 요청) 하나뿐 | T2 → T6 순서가 지켜지면 안전. 계획 순서가 그렇다 |
| T1·T2·T3·T5·T6 | 전부 `test_adapter_jibot_v3_order.py` 에 클래스를 **추가**만 한다 | 충돌 없음 |

### 태스크별 자기 일관성

| 태스크 | 테스트가 요구하는 것 ↔ 코드가 제공하는 것 | 결과 |
|---|---|---|
| T1 | `is_driving`/`start_driving`/`stop_driving` 3개 요구, 3개 구현 | 일치 |
| T2 | `_clear_edge_states_up_to` 요구, 구현 있음. 테스트가 `SimpleNamespace` 로 state 를 대체 | 일치 |
| T3 | `path_control`/`waypoint_pass_radius_mm`/`_continuous_path_enabled` 요구, 전부 구현 | 일치 |
| T4 | `segment_passes_within` 5케이스 요구, 구현 있음(`prev_xy=None` 폴백 포함) | 일치 |
| T5 | `drivable_run` 5케이스 + `_should_settle_at`/`_step_has_blocking_action`/`_is_coalescing_breaker` 요구, 전부 정의됨 | 일치. 단 Ruling 1 |
| T6 | `_set_new_base_request_from_queue` 3케이스 요구, 구현 있음 | 일치 |

## 사전 Ruling

**Ruling 1 (T4 → T5 미배선):** T4 가 만드는 `segment_passes_within` 을 T5 코드가
아무 데서도 호출하지 않는다. 그대로 두면 spec §3.4(폴링 주기 200mm vs 도착존 ±40mm)가
미해결로 남고 — spec 이 "이것 없이는 CP 가 동작하지 않는다"고 명시한 위험이다 —
T4 는 죽은 코드가 된다. spec 이 구속력 있는 권위이므로 계획을 보정한다.

결정: T5 에 스텝을 추가한다. 중간 노드 전용 대기 `_wait_until_node_passed(node, target)`
를 만들어 `segment_passes_within` 으로 판정하고, 구간 마지막 노드는 기존
`_wait_until_node_position_reached` 를 그대로 쓴다. 반경은
`waypoint_pass_radius_mm` 가 0 이면 기존 도착존으로 폴백한다.
비용: 틀리면 중간 노드 통과 판정이 기존 점 판정으로 남아 고속 구간에서 노드를 놓친다.
되돌리기는 T5 커밋 하나를 revert 하면 된다.

**Ruling 2 (`order_queue._queue` 접근):** T5 `_is_run_end` 와 T6 의 계수 코드가
`asyncio.Queue` 의 비공개 `_queue` deque 를 읽는다. 읽기 전용이고 파이썬 표준
라이브러리에서 안정적인 속성이지만 비공개다. 리뷰어가 지적할 수 있다.
결정: 그대로 둔다 — 대안(별도 pending 리스트 유지)은 큐와 이중 상태가 되어 더 나쁘다.
비용: 파이썬 버전이 올라가며 `_queue` 가 사라지면 깨진다. 그때는 큐를 감싸는
접근자 하나를 추가하면 된다.

## 진행

(태스크 완료 시 여기에 추가)

## 실행 중 유입된 외부 검토 (2026-08-27)

다른 세션이 WCS FMS 저장소에 쓴 검토 문서를 사용자가 전달:
`/Users/gihwan/workspaces/wcs-nodejs/docs/reference/fms/amr-node-coalescing-review.md` (227줄)

어댑터 쪽 주장 5개를 코드로 직접 검증한 결과 **전부 사실**:

| 주장 | 검증 |
|---|---|
| 지뢰1: `_active_order_worker_step_is_obsolete` 가 `seq <= last_node_sequence_id` 면 워커 cancel | 확인. `:4349` 에서 호출. 이미 `_order_action_step_in_flight` 예외가 같은 부류 버그를 막고 있음 |
| 지뢰2: `_rebuild_v3_order_queue_from_state` 가 접두부만 skip | 확인. 중간 통과 보고가 자기 방어에 필수 |
| `last_node_release_xy` 기본값이 0.0 이 아니라 500.0 | 확인 (config.toml:76, config.py:265) |
| 모르는 order actionType → FAILED → FATAL | 확인 (`:4882` "Unsupported order action type") |
| per-robot `robot { config = ... }` 는 overlay 가 아니라 통째 교체 | 문서 주장 채택(코드 미검증). spec 문구를 단정에서 경고로 바꿈 |

**Ruling 3 (지뢰 1 대응):** spec §5.2.1 신설 + plan Task 5 Step 9 추가.
`_coalescing_run_step` 을 두고 `_active_order_worker_step_is_obsolete` 에 예외를 넣는다.
근거: 이 예외는 발명이 아니라 같은 함수의 기존 `_order_action_step_in_flight` 선례를 따르는 것.
비용: 예외가 너무 넓으면 진짜 obsolete 스텝을 못 끊는다. 구간 주행 중인 스텝 하나로 한정한다.

**Ruling 4 (인프라 노드):** 어댑터 단독으로 식별 불가. 코드로 막지 않고 spec §5.2 에
미해결 위험으로 명시하고 "현장 적용은 인프라 노드 없는 구간부터"라는 운영 절차로 다룬다.
근거: 정보 소유자가 FMS 이고, 어댑터가 추측으로 병합을 좁히면 이득이 사라진다.
비용: 인프라 노드를 병합해 건너뛰면 문/리프트 호출이 영구 유실된다. 파일럿 구간 선정이 방어선.

**Ruling 5 (edgeDrivingMode FATAL, 문서 §7):** 이번 계획 범위 밖으로 둔다.
CP 와 독립이고 별도 태스크다. 사용자에게 별건으로 보고한다.
비용: FMS 가 먼저 고치면 전 차량에 FATAL 이 뜬다 — 순서가 뒤집히면 노이즈가 난다.

**Ruling 6 (창 산술):** spec §2.2 에 WCS FMS 의 4/2/1 창을 추가. 규칙은 그대로
"released 구간 끝까지" 하나이므로 계획 변경 없음. 혼잡 시 창이 1로 줄어 자동으로 현행
동작으로 돌아간다는 성질을 안전 게이트 불필요 근거로 기록.

Task 1: complete (commits 37e2327..85d3ed9, 리뷰 미실시 — 외부 검토 유입으로 중단)

## Task 1 리뷰 결과

리뷰어 판정: spec ❌ / quality Changes requested.
브리프가 요구한 것(`start_driving`/`stop_driving`/`is_driving`/`ContinuousPathHarnessTests`)은
정확히 구현됐고, `is_driving()` 이 프로덕션 `_has_active_automatic_motion` 의
`"goto" in mode` 분기와 일치함을 리뷰어가 코드로 확인했다. `stop_driving()` 도
`FakeVehicle.__init__` 기본값과 정확히 같다. 727 통과.

**Critical: 커밋 85d3ed9 에 다른 세션의 미커밋 작업이 섞였다.**
69줄 추가 중 30줄만 우리 것이고, 나머지는 dock charge handover 배선
(`FakeChargeCircuit` import, `charge_relay_held`, `um_stop` 분기,
`_VehicleLinkedChargeCircuit`, `_make_adapter` 의 charge/handover 설정).
구현자가 `git add <파일>` 로 파일 전체를 스테이징해서 딸려 들어갔다.
원인은 내 디스패치가 "이 파일은 다른 세션이 동시 편집 중"이라고 알려주지 않은 것이다.

부작용: 그 커밋만 체크아웃하면 `config.dock.handover_hold_settle_sec` 가 없어 테스트가 깨진다
(그 필드는 config.py 미커밋분에만 있다). 워킹트리에서는 727 통과라 드러나지 않는다.

**Ruling 7 (커밋 혼입):** 사용자가 세 선택지(커밋 재작성 / 직접 실행 / 그대로 두기) 중
**그대로 두기**를 선택했다. 히스토리를 재작성하지 않는다.
근거: 푸시 전이라 되돌릴 수 있고, 데이터 유실이 아니라 이력 혼동이며, 사용자가 두 세션의
사정을 아는 당사자다.
비용: 다른 세션이 자기 작업을 커밋하려 할 때 이미 남의 커밋에 들어가 있어 혼란스럽다.
`85d3ed9` 단독 체크아웃은 테스트가 깨진다. 되돌리려면 push 전에
`git reset --mixed HEAD~1` 후 재커밋하면 된다(워킹트리 미접촉).

**Ruling 8 (남은 태스크의 스테이징):** 남은 5개 태스크도 같은 공유 파일을 건드린다.
수술적 스테이징을 값싼 모델에 요구하는 대신, 디스패치에 "다른 세션이 동시 편집 중이고
커밋 혼입은 감수하기로 결정됐다. 대신 **네가 바꾼 것이 무엇인지 리포트에 정확히 적어라**"
를 넣어 추적성만 확보한다.
비용: 커밋 단위 정확도를 포기한다. 리포트가 실제 변경의 기록이 된다.

Task 1: minor (deferred): 새 테스트가 `_has_active_automatic_motion()` 을 직접 호출해
검증하지 않고 인라인 주석의 가정에 의존한다. 프로덕션 로직이 바뀌면 하네스 불일치를 못 잡는다.
Task 1: parked — 커밋 혼입 — Ruling: 위 Ruling 7. 사용자 결정으로 그대로 둔다.
Task 1: complete (commits 37e2327..85d3ed9, 1 parked, 1 deferred minor)

## Task 2 — 구현자 API 오류로 중단 후 재개

첫 디스패치(sonnet)가 tool_uses 65회 지점에서 API 오류로 종료됐다. 코드 변경은
워킹트리에 살아남았고 커밋과 리포트만 없었다. 컨트롤러가 상태를 검증:

- `_clear_edge_states_up_to` → adapter_jibot.py:6560
- `_finalize_v3_node_step` 호출 → :6010
- `_clear_v3_order_step` 주석 → :6595
- `EdgeStateReleaseTimingTests` → test_adapter_jibot_v3_order.py:12904
- `-k EdgeStateReleaseTiming` 1 passed

같은 에이전트를 SendMessage 로 재개해 (1) 전체 회귀 (2) 커밋 (3) 리포트를 맡겼다.
중단 직전 에이전트가 남긴 미완 관심사(다른 edge_states 쓰기 지점 ~3929/3998-4003/8926 가
새 타이밍 계약과 충돌하는지)도 함께 확인하도록 넘겼다.

**Ruling 9 (전체 회귀 실행 주체):** `scripts/run-tests.sh` 전체 실행이 **7분을 넘긴다**
(736 테스트, 2026-08-27 실측). 구현자가 이걸 기다리다 두 번 멈췄다(첫 번째는 API 오류,
두 번째는 대기 중 턴 종료).
결정: **구현자는 자기 파일 대상 타깃 테스트만 돌리고, 전체 회귀는 컨트롤러가 병렬로 돌린다.**
계획서의 "Run: scripts/run-tests.sh" 스텝은 이 분담으로 읽는다.
근거: 구현자를 7분 블로킹시키면 루프가 매 태스크마다 멈춘다. 회귀 자체는 포기하지 않는다 —
실행 주체만 옮긴다.
비용: 커밋이 전체 회귀 확인 **전에** 일어난다. 회귀가 나면 fix 루프에서 잡는다.

## 회귀 기준선 확정 (2026-08-27)

컨트롤러 전체 실행: **14 failed / 2276 passed, 508.84s (8분 29초)**.

실패 14건은 전부 기존 실패이며 우리 변경과 무관하다:
- `test_airshower_pairing_handover.py` 3건
- `test_airshower_passage_recipes.py` 2건
- `test_recipes_config.py` 5건
- `test_unknown_config_key.py` 4건

근거 두 가지 — (a) 네 파일 모두 `edge_states` / `_finalize_v3_node_step` /
`_clear_edge_states_up_to` / `start_driving` 를 참조하지 않는다(grep 0건),
(b) 다른 세션이 바로 그 테스트들이 쓰는 `adaptor/config/config.py`,
`config.toml`, `extensions.hcl`, `recipes.hcl`, `extensions/pio/__init__.py` 를
미커밋으로 편집 중이다.

**Ruling 10 (회귀 기준선):** 이 14건을 기준선으로 고정한다. 이후 태스크의 합격선은
"이 14건 그대로, 그 이상 없음"이다. 다른 세션의 미커밋 작업이 원인이므로 우리가 고치지 않는다.
비용: 그 14건 뒤에 우리 회귀가 숨으면 못 본다. 태스크마다 실패 목록을 비교해 방어한다.

## Task 2 리뷰 결과

리뷰어 판정: **spec ✅ / quality Approved**.
경계조건 검증됨 — `>=` 유지가 기존 `_prune_v3_order_state_before_last_node` 의
`> last_sequence_id` 선택과 같은 집합을 고르고, 발신 state 투영(`:3481-3483`)이
필터 없는 pass-through 라 더 오래 남은 edgeState 가 실제로 FMS 에 도달한다.
즉 이 수정은 겉치레가 아니라 기능적으로 실재한다.

Important 1건 → fix 라운드 1 진행:
- `EdgeStateReleaseTimingTests` 가 `_clear_edge_states_up_to` 만 단위 검증하고,
  `_finalize_v3_node_step` 이 그걸 부르는지, `_clear_v3_order_step` 이 더는 엣지를
  안 지우는지를 고정하지 않는다. 삭제한 `else` 분기를 되살려도 테스트가 초록이다.

**Ruling 11 (로그 태그 충돌):** 리뷰어가 Minor 로 분류했고 브리프 스니펫에서 온 것이라
내 몫이다. minor 는 원래 루프에 안 넣지만 이미 fix 디스패치가 필요하므로 함께 넣는다.
`_finalize_v3_node_step` 쪽만 `[ORDER EDGE RELEASE]` 로 개명하고
`_process_v3_edge_step` 의 기존 태그는 둔다.
비용: 로그를 파싱하는 도구가 있었다면 태그가 바뀐다. 이 태그는 신규라 소비자가 없다.

Task 2: minor (deferred): `_clear_v3_order_step` 의 before_edges/after_edges 카운터가
이제 항상 같은 값이라 사실상 죽은 필드다. 의도적 보존이며 비차단.
Task 2: minor (deferred): orderUpdate 재큐 경쟁 창이 몇 틱에서 노드 간 주행 시간 전체로
넓어졌다. 리뷰어가 무해(idempotent no-op)로 확인. **Task 5 가 이 창을 더 넓히므로 인지 필요.**

Task 2: fix round 1/5 (2 addressed, 0 open — 배선 고정 테스트 2개 추가, 로그 태그 개명; commits d042a52..b7298c9)
Task 2: minor (deferred): `_prune_v3_order_state_before_last_node` 는 `>` 를 쓰고 이 수정은 `>=` 를 쓴다.
  node/edge sequenceId 가 짝/홀로 갈려 같은 집합이라는 논거인데, **Task 5(코얼레싱)가 착륙할 때
  다시 볼 것.** 코얼레싱은 노드를 건너뛰므로 그 전제가 흔들릴 수 있다.
Task 2: complete (commits 85d3ed9..b7298c9, review clean)

## Task 3 디스패치 시 주의

다른 세션이 `adaptor/config/config.py` 와 `config.toml` 을 미커밋 편집 중이고,
기준선 실패 14건 중 4건이 바로 `test_unknown_config_key.py` 다. Task 3 이 같은 두 파일에
키를 추가하므로 혼입과 오진 위험이 가장 높은 태스크다. 디스패치에 명시한다.

## Task 3 결과

리뷰어 판정: **spec ✅ / quality Approved**. Critical/Important 0건.
직접 확인된 것 — `path_control`/`waypoint_pass_radius_mm` 가 config.py:379,384 와
config.toml:107,109 양쪽에 같은 이름·타입·기본값으로 존재(부팅 실패 위험 없음),
`_warned_path_control_values` 가 `__init__:335` 에 실제로 초기화됨(AttributeError 위험 없음),
경고가 값당 1회만 나감, `_continuous_path_enabled` 가 None/빈문자열/대소문자 혼용을 안전 처리,
그리고 **이 커밋은 오늘 런타임 동작을 전혀 바꾸지 않는다**(게이트를 읽는 곳이 아직 없음 — grep 확인).
`test_unknown_config_key.py` 실패가 기준선 4건과 이름·개수 완전 일치.

Task 3: minor (deferred): `_continuous_path_enabled()` 가 선례로 든 `_nearest_node_mode()` 의
`getattr(...) or ""` 관용구를 생략하고 `str(None)` 에 의존한다. 동작은 안전하나 선례와 미묘하게 다르다.
Task 3: minor (deferred): TDD 순서가 실제로는 구현 우선이었다(구현 -> 테스트 -> 임시 제거로 실패 확인).
리뷰어가 테스트의 진짜 실패를 dataclass 속성 논거로 확인했으므로 결과는 유효하다.
Task 3: complete (commits b7298c9..884c78a, review clean)

## Task 3 이후 전체 회귀 + 기준선 갱신

**15 failed / 2280 passed (504s).** 기준선 14건 + 신규 1건:
`tests/test_senders.py::UdsSenderTest::test_round_trip_delivered`.

단독 재실행하면 **4/4 통과**한다. UDS 소켓 왕복 테스트라 전체 스위트 부하에서만 깨지는
flaky 다. 우리 변경(config 키 2개 추가)과 인과가 없다.

**Ruling 12 (flaky 편입):** 기준선을 "14건 + test_senders UDS 왕복 flaky 1건"으로 갱신한다.
이후 태스크의 합격선은 "이 15건 이내, 신규 이름 없음"이다.
근거: 단독 통과가 인과 부재의 증거다.
비용: 진짜 회귀가 test_senders 에 숨으면 못 본다. 신규 실패가 나오면 항상 단독 재실행으로 가른다.

## Task 4 결과

리뷰어 판정: spec ✅ / quality **Changes requested (Important 1건)**.

기하는 손계산 2건으로 검증됨(내부 t=0.3 케이스, t<0 클램프 케이스 모두 정확).
그런데 **클램프가 테스트로 고정돼 있지 않다** — 5개 테스트 중 t 가 [0,1] 밖으로 나가면서
결과가 달라지는 케이스가 하나도 없어, `t = max(0, min(1, t))` 를 지워도 전부 초록이다.
리뷰어 반례: `segment_passes_within((0,0),(5,5),(10,10),1)` — 클램프 있으면 거리 7.071(False),
없으면 무한 직선까지 거리 0(True, 오탐). 컨트롤러가 직접 실행해 7.071/False 확인했다.

**Ruling 13 (클램프 테스트 추가):** 이 5개 목록은 브리프 Step 1 이 명시한 것이라
구현자 이탈이 아니라 **계획의 갭**이고, 따라서 내 몫이다.
spec §5.1 이 보간 판정을 "이것 없이는 CP 가 동작하지 않는다"고 명시한 메커니즘인데
테스트가 그 메커니즘을 보호하지 못한다. spec 이 구속력 있는 권위이므로 테스트를 추가한다.
`point_to_segment_distance` 직접 검증도 함께 넣는다 — Task 5 가 소비하는 선언된 인터페이스다.
비용: 없음. 테스트만 는다.

Task 4: minor (deferred): 과거 `adapter_jibot_.py:970` `segment_intersects_zone` 에 같은
알고리즘이 있었으나 호출부가 주석 처리된 죽은 코드이고 현행 어댑터에는 대응 함수가 없다.
실질 중복 아님. `utils/helpers.py:85 get_distance` 는 점-점 거리라 겹치지 않는다.

Task 4: fix round 1/5 (2 addressed, 0 open — 클램프 고정 테스트 + point_to_segment_distance 직접 검증; commits d5df1ab..d68de57)
재리뷰가 손계산으로 확인: node=(0,0), seg=(5,5)-(10,10) 에서 raw t=-1.
클램프 시 최근접(5,5) 거리 7.071 → False, 클램프 제거 시 최근접(0,0) 거리 0 → True.
assertFalse 가 실제로 뒤집히므로 클램프가 진짜로 고정됐다.
Task 4: minor (deferred): 새 테스트 주석의 "2026-08-26 검토 시 발견"은 실측 로그값이 아니라
검토 일자 메모다. CLAUDE.md 의 "실측 근거(날짜·호스트·로그값)" 기준에 다소 못 미친다.
Task 4: minor (deferred): 클램프 **상한**(t>1) 케이스는 여전히 미검증. 하한만 고정됐다.
Task 4: complete (commits 884c78a..d68de57, review clean)

## Task 5 브리프 결함 — 내 편집 충돌 (2026-08-27)

브리프를 뽑아 보니 결함 3건이 있었다. 전부 내 계획 편집이 서로 덮어쓴 결과다:
- **Ruling 3(지뢰 1 가드)이 통째로 사라짐** — 앞서 추가했는데 이후 번호 재정렬 편집이 클로버했다
- Ruling 1(`segment_passes_within` 배선)은 Consumes 줄에만 있고 **실행 스텝이 없었다**
  → Task 4 가 죽은 코드가 되고 spec §3.4 위험이 미해결로 남을 뻔했다
- Step 번호가 8, 8, 10, 11 로 깨져 있었다

**Ruling 14 (브리프 수리):** 계획서 Task 5 를 수리하고 브리프를 재생성했다.
Step 9(`_wait_until_node_passed` 배선)와 Step 10(지뢰 1 가드)을 삽입하고 1~13 으로 재번호.
Step 9 에 "새 pose 소스를 발명하지 마라 — 기존 경로와 다른 좌표계를 쓰면 판정이 어긋난다"를
명시했다(브리프가 `_vehicle_xy()` 를 가정하는데 실재 여부가 불확실하기 때문).
비용: 브리프가 424줄로 늘어 구현자 부담이 커졌다. 대신 두 Ruling 이 유실되지 않는다.

교훈: 계획서를 여러 번 부분 편집하면 스텝이 조용히 사라진다.
**디스패치 직전에 브리프의 스텝 번호 연속성과 Ruling 반영 여부를 grep 으로 확인할 것.**

## Task 5 리뷰 — Critical 1건은 내 계획 결함

리뷰어 판정: **spec ❌ / quality Changes requested**.

**Critical: `continuous` 에서 노드마다 UmGoto 를 쏜다.** `_send_node_motion(node)`(`:6681`)이
코얼레싱 게이트 없이 무조건 실행되고, 이 diff 가 추가한 분기(`:6713-6727`)는 goto 발행 여부가
아니라 **대기 방식**만 가른다. 즉 4노드 구간이면 UmGoto 4발이고 매번 살아 있는 goto 위에 얹는다.
spec §4 CP-2("마지막 노드 하나로만 goto")와 §5.2.1(anchor = 구간 마지막)이 **미구현**이다.
CP-1(무정지 통과)만 들어갔다.

구현자가 정확히 escalate 했고 리뷰어가 독립적으로 같은 결론에 도달했다. 브리프가 스펙보다
좁았고 구현자는 브리프를 따른 것이 맞다(YAGNI). **계획 결함이다.**

**Ruling 15 (CP-2 anchor — 사용자 판단 필요):** 코드로 결정하지 않는다.
두 길 다 대가가 있고, 내 실측이 오히려 스펙 전제를 흔든다:
- **구간 끝 하나로 goto**(스펙대로): 2026-08-26 실측이 지지한다(p40->p58 단일 goto 가 p59 를
  1002mm/s 무감속 통과). **그러나** 같은 실측에서 로봇이 p38 을 요청받자 p39->p40->p37->p38 로
  **자기 경로를 골랐다.** 즉 FMS 가 승인한 노드 순서를 안 따를 수 있고, 그건 점유 관제 위반이다.
  내 스펙 §4 는 이 점을 계산에 넣지 않았다.
- **노드마다 goto**(현 구현): FMS 경로 순서를 강제하지만 goto 겹침이 **미측정**이다.
  P2-3(주행 중 UmGoto 오버랩) 프로브는 끝내 실행하지 않았고, 앞선 조사는 연속 UmGoto 가
  큐잉이 아니라 **덮어쓰기일 가능성이 높다**고 봤다. 덮어쓰기라면 현 구현이 사실상 동작하지만
  근거가 없다.
비용: 잘못 고르면 (전자) 관제 위반, (후자) 미측정 동작에 의존. 게이트가 기본 off 라
출하되지는 않는다. 해소 수단은 실기 프로브 1건이다.

Task 5: fix round 1/5 진행 중 — Important 3건(엣지 블로킹 breaker 유실, `_wait_until_node_passed`
무한 대기, look-ahead predecessor) + 테스트 갭 2건(vacuous 테스트, 선분 보간 미실행).

## Task 5 이후 전체 회귀

**14 failed / 2306 passed (496s).** 정확히 기준선 14건이고 신규 실패 0건.
`test_senders` UDS flaky 는 재현되지 않아 Ruling 12 의 flake 판정이 확인됐다.
통과 수가 2280 -> 2306 (+26) — Task 5 가 추가한 테스트다.
즉 코얼레싱 코드는 `stop_point` 기본값에서 회귀를 일으키지 않는다(리뷰어도 무변경 확인).

Task 5: fix round 1/5 (4 addressed, 0 open — 엣지 블로킹 breaker 복원, 공유 stall guard,
look-ahead predecessor, 테스트 갭 2건; commits 8010922..06cfe92)

재리뷰 확인 사항:
- `_NodeMotionStallGuard` 추출이 **진짜 순수 추출**이다 — stall 시계 -> WARNING -> 재전송 ->
  retry 한도 -> FATAL 순서와 조건이 동일하고 로그 문자열 4개가 byte-identical.
  cosmetic delta 2건(station 읽는 위치, `_is_jibot_stopped` 호출 횟수)은 사이에 await 가 없어 무해.
- `stop_point` 무영향 확인 — `_is_run_end` 가 look-ahead 전에 True 로 단락한다.
- `drivable_run` 시그니처 무손상, `prev_node_id` 가 세 헬퍼 모두 기본 None 이고 프로덕션 호출부
  두 곳이 생략하므로 오늘 동작 유지 → **Task 6 영향 없음.**
- 구현자가 pre-fix 구현을 몽키패치해 5개 고정 테스트의 FAIL 을 실증했고, 재리뷰가 그중 2건을
  독립적으로 재추론해 확인했다.

Task 5: minor (deferred): `_resend_node_goto` docstring 이 "유일한 호출자는
`_wait_until_node_position_reached`" 라고 하는데 이제 공유 guard 를 통해 통과 대기에서도 도달한다.
안전성 논거는 유지되나 문장이 거짓이다.
Task 5: minor (deferred): 스트래들 테스트의 `never_inside` 계산이 표본이 아니라 리터럴을
재계산해 동어반복이다. 하중을 지는 단정은 별도로 유효하다.
Task 5: minor (deferred): `_coalescing_run_step` 가드가 아직 스스로 발동할 수 없다
(finalize 가 대기 뒤에 돈다). 방어 코드로 남는다.
Task 5: minor (deferred): pose 결측 구간에서 `prev_xy` 가 전진하지 않아 통과 판정이 이르게 날 수 있다.
Task 5: minor (deferred): `JIBOT_NODE_UNREACHED` 에 명시적 해제 경로가 없다(기존 문제).
Task 5: parked — spec ❌ CP-2 본체 미구현 — Ruling: 위 Ruling 15. 실기 프로브 1건으로 갈린다.
Task 5: complete (commits d68de57..06cfe92, 1 parked, 5 deferred minors)

## Task 6 리뷰 결과

리뷰어 판정: **spec ✅ / quality Approved**.
발행 경로를 끝까지 추적해 `state.new_base_request` 를 `None` 으로 덮는 지점이 초기화 외에
없음을 확인했고, 인접 계약(lastNodeSequenceId 가 액션 전에 오름) 유지와 `stop_point` 무변화도
확인했다. 계수도 정확 — `_process_v3_order_queue` 가 현재 스텝을 먼저 pop 하므로 finalize 시점
큐에 자기 노드가 없고, `kind=="node"` 필터가 엣지를 배제한다.
구현자의 편차 4건(방어적 getattr, sync TestCase, 로컬 _make_adapter, 추가 배선 테스트) 전부 정당 판정.
추가한 배선 테스트가 스텁이 아니라 실제 프로덕션 카운팅 경로를 탄다는 것도 확인됐다.

**Ruling 16 (오더 종료 시 플래그 미해제 — 리뷰어 defer 권고를 뒤집음):**
리뷰어는 defer 를 권고했으나 fix 하게 했다.
근거: `newBaseRequest` 의 의미는 "지금 base 를 늘려 달라"다. 오더가 끝나 로봇이 유휴인데
true 가 남아 있으면 그건 노이즈가 아니라 **로봇 상태에 대한 거짓말**이다. 우리가 새로
도입하는 신호를 반쪽만 정의한 채 두면 안 보내느니만 못하다. 수정 범위도 작다.
해제는 `continuous` 게이트와 무관하게 무조건 걸도록 지시했다 — 게이트로 막으면 오더 사이에
설정을 토글했을 때 플래그가 갇힌다.
비용: 최종 리뷰 전에 fix 라운드가 하나 더 든다. 안 고치면 FMS 가 유휴 로봇의 base 확장을
요청받는다.

## Task 6 이후 전체 회귀

**14 failed / 2317 passed (500s).** 기준선 그대로, 신규 0건. 통과 2306 -> 2317 (+11).

전 구간 회귀 추이 — 한 번도 늘지 않았다:
| 시점 | passed | failed |
|---|---|---|
| Task 3 후 | 2280 | 15 (기준선 14 + test_senders flaky 1) |
| Task 5 후 | 2306 | 14 |
| Task 6 후 | 2317 | 14 |

Task 6: fix round 1/5 (1 addressed, 0 open — 공용 setter 경유 무조건 해제, 오더 완료·취소 2배선; commits 7e4006b..4d15837)
재리뷰가 확인: 해제에 `continuous` 게이트 누수 없음, 두 배선 모두 공용 setter 경유,
`_set_new_base_request_from_queue` 위임이 동치(로그 포맷만 변경), 두 테스트가 각 배선을 독립 pin.
CancelledError 미포함이 의도된 설계임도 코드로 검증 — 진짜 취소는 `_cancel_order_on_loop:9193`,
크래시는 `_drop_order_after_worker_crash:9290` 가 각각 `_clear_cancelled_order_state` 를 부르고,
obsolete-worker 재시작만 두 경로를 안 타는데 같은 오더가 이어지므로 플래그 보존이 맞다.
Task 6: minor (deferred): `_accept_v3_order_for_queue:4054` 도 worker 를 cancel 하는 세 번째 경로다.
보통 order-complete 해제가 먼저 돌았겠지만 두 상태 갱신의 동기화 보장은 미확인.
Task 6: complete (commits 06cfe92..4d15837, review clean)

# 전 태스크 완료 — 최종 브랜치 리뷰로 진행

## 최종 브랜치 리뷰 (37e2327..4d15837, 10커밋)

spec 이행: CP-1 ✅ / **CP-2 ❌(park)** / CP-3 ✅ / **CP-4 ⚠️ 이행됐으나 게이트 밖**

최종 리뷰가 태스크별 리뷰가 구조적으로 못 볼 것을 찾았다:
**CP-4 가 `_continuous_path_enabled()` 게이트 없이 무조건 돈다.** 즉 `stop_point`(기본값)에서도
엣지가 A->B 주행 내내 살아 있다가 다음 노드 통과 때 빠진다. 이걸 보는 소비자가 여럿이다 —
MQTT state 발행(:3614), `_is_v3_order_finished`(:4439, 오더 완료 타이밍), 큐 재구성(:4419),
WebUI(web/render.py:569,1021), 모니터. 배포가 `--config-toml-mode keep` 이라 **아무도 옵트인하지
않은 채 전 차량에 적용**된다. "기본값 off 라 안 바뀐다"는 내 서사가 여기서만 틀렸다.

방향은 안전한 쪽이다(점유를 더 오래 잡음 -> 실패 모드가 충돌이 아니라 처리량 감소).
**사용자 결정 대기 중** — A: 그대로 두고 배포 시 공지 / B: 게이트를 씌움.

머지 판정: **CP-2 park 는 머지를 막지 않음**(게이트 off 라 프로덕션 도달 불가, 성급한 수정이 더
위험). **CP-4 게이팅은 사용자 사인오프 필요.**

CP-2 착륙 시 반드시 같이 고칠 것 (최종 리뷰 발견):
`goto_recoverable = self._active_goto_node is node` 가 pass 경로에서 True 라, goto 가 구간
마지막 노드를 겨냥하는 순간 stall 재전송이 **이미 지나온 중간 노드로 로봇을 되돌려 보낸다.**

## 최종 fix 웨이브 (4d15837..3b6966e)

3건 전부 ADDRESSED, 신규 파손 0. 770 passed (768+2).
1. `_wait_until_node_passed` 가 `bool` 반환 — `gave_up` 시 False -> 기존 requeue-and-stop-worker
   경로로 합류. 재리뷰가 호출 사슬을 끝까지 추적해 타이트 루프 없이 실제 정지함을 확인.
2. `_resend_node_goto` docstring 정정 — 2번째 호출자에 대한 안전 논거를 재리뷰가 직접 검증
   (move 룰 노드는 breaker 라 통과 대기에 애초에 안 오고, dock-work 노드는 다른 분기로 감).
3. `prev_xy` staleness bound (`poll_sec * 3`) — 측정 대상이 "마지막 유효 표본 이후"가 맞고
   `None` 폴백이 실제 점 판정으로 떨어짐을 확인.

판별 검사가 특히 좋았다 — Finding 3 은 되돌렸을 때 실제 오탐 로그
`[ORDER NODE PASSED] at=(100.0, 1400.0)` 를 뽑아 버그를 실증했다.

재리뷰가 fix 리포트의 오류도 정정: `_wait_until_node_position_reached` 호출부는 3곳이 아니라
2곳(:6837, :6912)이다.

**Ruling 17 (`_wait_until_node_position_reached` 방치):** 같은 무한 대기 모양이 기본 경로에도
있으나 `git show 37e2327` 확인 결과 **브랜치 이전부터 있던 것**이고 이번 브랜치로 새로 도달
가능해진 게 아니다. 고치면 `stop_point` 동작이 바뀐다. 별도 티켓으로 올린다.
비용: 기본 경로의 무한 대기가 남는다. 사전 존재이므로 이 브랜치의 부채는 아니다.

**Ruling 18 (`_PASS_WAIT_STALE_GAP_POLLS = 3.0`):** 실측 근거 없는 판단값이나 그대로 둔다.
너무 낮으면 통과를 놓쳐 구간 끝에서 후진 재전송, 너무 높으면 오판으로 FMS 위치 오보 —
오판 쪽이 더 나쁘므로 관대한 3.0 이 옳은 방향이다. `[ORDER NODE PASS STALE]` 로그가 `gap=` 을
찍으므로 현장 데이터가 자동으로 쌓인다. 관측 후 조정.
비용: 현장에서 값이 안 맞으면 통과 누락이 난다. 로그로 잡힌다.

deferred (최종 리뷰): `_resend_node_goto` docstring 의 첫 호출자 서술이 과잉 일반화(원본 영문
docstring 이 이미 갖고 있던 부정확성). dropout 테스트의 `reappeared` 단정이 간접적.

**Ruling 19 (CP-4 게이팅 — 사용자 "안전한 방향으로"):** 게이트를 씌우지 않는다.
두 방향의 실패 모드가 대칭이 아니다 — 이전 동작(조기 해제)은 FMS 가 구간을 떠났다고 보는데
로봇이 아직 주행 중이라 **충돌 위험**이고, 이후 동작은 점유를 더 오래 잡아 **처리량 감소**다.
게이트를 씌우면 옵트인 전까지 위험한 쪽이 프로덕션에 남는다. CP-4 는 기능이 아니라
VDA5050 §6.6.2 위반의 수정이므로 기능 플래그 뒤에 두면 버그가 살아 있게 된다.
코드 변경 없음(이미 무조건). 대신 spec §4 CP-4 에 "게이트 밖"과 배포 공지 문구를 명시했고,
되돌리는 방법(호출 한 줄 삭제)도 적었다.
비용: 혼잡 구역에서 처리량이 떨어질 수 있다. 배포 공지가 방어선이다.
사용자 의도를 A(그대로 두기)로 읽었음을 명시하고 진행했다 — 반대였다면 되돌리기는 한 줄이다.
