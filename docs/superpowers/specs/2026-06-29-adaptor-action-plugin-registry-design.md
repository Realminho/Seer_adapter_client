# 어댑터 액션 플러그인 레지스트리 (hybrid 러너)

작성일: 2026-06-29
상태: 설계 승인 대기 (브레인스토밍 산출물)
범위: 1개 리포 — `unified-amr-adaptor`(어댑터)

## 1. 배경 / 동기

현재 instant action 디스패치는 `adapter_jibot.py`의 `instant_actions_accept_procedure`
안에 있는 거대한 `if/elif` 사다리(40+ 분기)다. 모든 분기는
`self._handle_*_instant_action(action)` 메서드를 호출하고, 그 핸들러들은
`self.state` / `self.config` / `self._vehicle`(JIBOT client) / `self._sound` /
MQTT publish(`request_state_publish`, `_update_instant_action_status`)에 직접
접근한다. 즉 액션 로직이 어댑터 상태에 깊게 결합된 채 한 메서드에 몰려 있다.

현장별/실험적 커스텀 액션을 추가하려면 매번 이 사다리를 수정해야 하고, 커스텀
코드의 품질 문제(예외 미처리, hang)가 메인 어댑터(로봇 제어·상태 발행)를 위협한다.
작성 주체는 **혼합**이다: 코어/모션 액션은 어댑터 팀이, 실험·현장별·위험한 액션은
외부(현장/SI)가 작성하며 후자는 repo 접근 없이 로봇에 파일/설정만 떨군다. 외부 코드가
메인 어댑터를 절대 죽이면 안 된다.

## 2. 목표 / 비목표

**목표**
- 액션 디스패치를 `if/elif` 사다리에서 **file-based action registry**(dict 디스패치)로
  전환할 토대를 만든다. 이게 실제 "토대"이며 실행 방식과 무관하게 즉시 이득.
- **통일된 핸들러 계약**(`ActionContext` → `ActionResult`)을 정의해, 실행 방식이
  달라도 같은 결과 타입을 내고 동일하게 VDA5050 `ActionStatus`로 매핑한다.
- **러너를 액션별 전략으로** 선택 가능하게 한다 (격리 스펙트럼, 리뷰 finding 4로 정제):
  - `inline` (full access) — 신뢰 코드, 어댑터 상태 직접 접근, 가장 빠름. hard timeout이
    필요하면 **async cooperative timeout**만(코루틴 취소 협조). live adapter를 쥔 thread를
    hard-kill 흉내내지 않는다.
  - `inline` + thread timeout — **read-only/restricted context**로 제한한 hang 격리
    (어댑터 write 금지). timeout 후 좀비 thread가 깨어나도 상태를 오염시키지 못한다.
  - `subprocess` — 외부/신뢰 못 함, 진짜 격리(segfault/네이티브 크래시/**hard-kill**).
    상태 write가 필요하면 이 러너 금지(정의상 inline).
- 커스텀 액션을 **repo 수정 없이** 추가 가능하게 한다 (in-process는 모듈 드롭, subprocess는 manifest 드롭).
- 기존 40+ 내장 핸들러를 **회귀 위험 0**으로 유지한 채 점진 이주.

**비목표 (이번에 안 함)**
- subprocess가 어댑터 상태를 **양방향으로 제어**하는 IPC 백채널. 격리 목적과 모순.
  subprocess 계약은 **단방향 입력(JSON) → 단방향 결과(JSON)**로 못 박는다. 로봇 제어가
  필요한 액션은 정의상 `inline`이어야 한다.
- 기존 40개 내장 핸들러를 한 번에 전부 registry로 재작성하는 것 (점진 이주만; 일괄 이주는 별도).
- order(step) 액션 경로 통합. 이번은 **instant action 경로**만 (order 액션은 후속).
  단 `ActionSpec`/`ActionResult`/`ActionContext` 네이밍·형태는 instant 전용 용어를 피하고
  **중립적**으로 유지해 후속 order 액션 통합에 재사용 가능하게 한다(리뷰 메모).
- 새로운 권한/샌드박싱(컨테이너, seccomp 등). subprocess 격리는 프로세스 경계 + timeout/kill 수준까지만.

## 3. 핵심 통찰 — 세 옵션은 두 직교 축을 섞고 있다

| 축 | 내용 | 가치 |
|---|---|---|
| **디스패치 구조** | action_type → 핸들러를 어떻게 찾나 (if/elif → registry dict) | 즉시·무조건 이득. 실행 방식과 무관 |
| **실행 전략** | 핸들러를 어떻게 돌리나 (inline / inline+timeout / subprocess) | 액션별 선택. registry 위에 얹는 것 |

"토대"는 실행 방식이 아니라 **registry**다. 코드엔 이미 in-process 핸들러 40+개가
있으므로 in-process는 "새로 만드는 것"이 아니라 "이미 있는 것을 registry로 정리"다.
subprocess는 그 registry에 **러너 한 종류를 더 꽂는 것**이다. 따라서 hybrid의 본질은
"두 시스템 병렬"이 아니라 **하나의 registry + 통일 계약 + 러너 전략**이고, 이 분해가
hybrid 비용을 ~2배에서 ~1.2배로 낮춘다.

## 4. 아키텍처 / 데이터 흐름

```
InstantActions (MQTT/WebUi)
  │
  ▼
instant_actions_accept_procedure  (adapter_jibot.py)
  │  내장 if/elif 사다리 (기존 40개, 그대로 유지)
  │      └─ 매칭 안 되면 ↓ (else 직전)
  ▼
ActionRegistry.dispatch(action, adapter)   ← 신규 통합 지점
  │  action_type 으로 ActionSpec 조회
  ▼
runner 분기 (ActionSpec.runner)         [dispatch는 sync wrapper, §9]
  ├─ inline      → InlineRunner    : ctx=live adapter, handler(ctx) 호출
  │                 · 짧은 sync = 그 자리에서 ActionResult
  │                 · async/timeout = RUNNING 선발행 → _run_on_adapter_loop (§9)
  │                 · hard-timeout thread는 read-only context로 제한 (finding 4)
  └─ subprocess  → SubprocessRunner: ctx=직렬화 스냅샷(JSON), 코드 import 안 함
                    RUNNING 선발행 → _run_on_adapter_loop:
                      asyncio.create_subprocess_exec + wait_for(timeout)
                      stdout JSON(FINISHED|FAILED) → ActionResult
  │
  ▼
ActionResult(status: FINISHED|FAILED, description)  →  _update_instant_action_status(...)
  → _clear_terminal_instant_action_states() → request_state_publish()
```

핵심: 두 러너가 같은 `ActionResult`를 반환하고, 디스패처가 그것을 기존
`_update_instant_action_status`로 흘려보낸다. 상태 발행·정리 로직은 기존 그대로 재사용.

## 5. 데이터 계약 — ActionContext / ActionResult

두 러너가 만족해야 하는 단일 계약. 차이는 **context의 풍부함**뿐.

```python
@dataclass(frozen=True)
class ActionResult:
    # 러너의 **최종** 반환은 FINISHED | FAILED 로만 제한 (RUNNING 금지, 리뷰 finding 2).
    # 종료된 액션(특히 subprocess 종료)이 RUNNING으로 남는 stuck 상태를 원천 차단한다.
    # RUNNING은 dispatcher가 실행 시작 시 1회 발행하거나(§9), 장기 inline 핸들러가
    # ctx.report(desc)로 내는 **중간** 상태로만 허용한다 — 러너의 terminal 값이 아니다.
    status: ActionStatus          # FINISHED | FAILED (terminal only)
    description: str = ""
```

**inline context** — 살아있는 어댑터 핸들을 그대로 노출:
```python
class InlineActionContext:
    action: Any                   # action_id / action_type / action_parameters
    params: Dict[str, str]        # {p.key: p.value}
    adapter: "JibotAdapter"       # state/config/_vehicle/_sound/publish 직접 접근
    def report(self, description: str) -> None: ...   # 중간 RUNNING 발행
```

**subprocess context** — 직렬화 스냅샷(JSON), 어댑터 객체 없음:
```python
# 자식에게 stdin(JSON)으로 전달:
{
  "action_id": "...", "action_type": "...",
  "params": {...},
  "snapshot": {                 # 읽기 전용 기본 화이트리스트 (리뷰 finding 5)
    "serial_number": "...", "simulation": false,
    "current_map_id": "...", "last_node_id": "...",
    "last_node_sequence_id": 0, "pose": {...},
    "order_id": "...", "paused": false, "work_in_progress": null
  }
}
# 자식이 stdout(JSON 한 줄)으로 반환 (terminal only):
{ "status": "FINISHED" | "FAILED", "description": "..." }
```

**snapshot 화이트리스트 기본값 (finding 5)**: 위 필드만 노출한다. **제외**: broker
credential, filesystem path, full config, raw robot telemetry dump. 추가 필드는 해당
액션의 `ActionSpec`에서 **명시적 opt-in**으로만 더한다(기본은 최소 노출).

subprocess 핸들러는 어댑터 상태를 **읽기**만(스냅샷) 가능하고, 로봇/상태를 **쓰지** 못한다.
쓰기가 필요하면 그 액션은 `inline`이어야 한다(§2 비목표). subprocess stdout의 terminal
status는 FINISHED|FAILED 뿐이며, RUNNING은 dispatcher가 spawn 시점에 1회 발행한다(§9).

## 6. discovery 메커니즘 — 러너별로 다르다 (격리의 핵심)

subprocess의 목적은 "신뢰 못 할 코드가 메인을 안 죽이는 것"인데, 그 코드를 `import`해서
등록하면 **import-time 코드가 메인 프로세스에서 실행**되어 격리가 깨진다. 따라서 발견
방식을 러너별로 분리한다:

- **inline 액션** = 모듈을 import 하고 그 안의 `register`/`handle` 콜러블을 등록.
  신뢰 코드만 이 경로(어댑터 팀이 검토/머지한 것).
- **subprocess 액션** = **manifest만 읽는다**(TOML). 코드는 절대 import 하지 않고
  spawn만 한다. manifest = `action_type`, `command`(argv), `timeout_sec`, 선택적
  param schema.

→ 이 분리가 "신뢰 못 할 코드를 메인 프로세스에 안 들이는" 격리를 실제로 보장한다.

## 7. config / manifest 형식

`config.toml`(또는 `robots.toml` override)에 `[[actions]]` 테이블. 기존
`config.adapter.instances` 패턴을 그대로 따른다.

```toml
# inline: 어댑터 venv 안에서 import 되는 신뢰 핸들러
[[actions]]
action_type = "testSound"
runner = "inline"
module = "actions.sound.test_sound"   # actions/ 패키지 경로
timeout_sec = 0                        # 0 = 무제한(짧은 핸들러)
motion = false

# inline + timeout: motion=true(어댑터 write 필요)이므로 핸들러는 async cooperative.
# timeout은 코루틴 취소로 협조 처리한다(§9). thread executor 경로 아님(그건 read-only 전용).
[[actions]]
action_type = "customCalibrate"
runner = "inline"
module = "actions.calibrate"
timeout_sec = 30
motion = true

# subprocess: 외부/신뢰 못 함. 코드 import 안 함, spawn만.
[[actions]]
action_type = "customDoorOpen"
runner = "subprocess"
command = ["python", "actions/custom_door_open.py"]   # 어댑터 디렉토리 기준
timeout_sec = 10
motion = false
```

registry 메타(`action_type`, `motion`)는 `core/registry.py`의 `InstantAction` 튜플과
연결해 WebUi 버튼까지 자동 노출되게 한다(기존 `_JIBOT_INSTANT_ACTIONS`에 동적 합류).

## 8. 디스패치 통합 지점 + 마이그레이션

- 통합 지점: `instant_actions_accept_procedure`의 거대 `if/elif`에서 **마지막
  `else`(Unsupported) 직전**에 `if registry.has(action.action_type): registry.dispatch(...)`.
  기존 40개 분기는 **건드리지 않는다** → 즉시 가치, 회귀 위험 0.
- **Busy/motion gate 배선 (리뷰 finding 3)**: busy gate(`_work_in_progress is not None`
  + `_is_motion_instant_action(action)`)는 if/elif **앞**(adapter_jibot.py:4062)에서 돌아
  registry dispatch보다 **먼저**다. 따라서 `_is_motion_instant_action()`이
  **built-in motion set + registry의 motion=true**를 함께 보도록 확장한다
  (`... or registry.is_motion(action.action_type)`). 이 배선이 없으면 custom motion 액션이
  work BUSY 중에도 게이트를 우회해 실행된다.
- 디스패처가 inline 핸들러를 부를 땐 결과 `ActionResult`를 받아
  `_update_instant_action_status(action.action_id, result.status, result.description)`로
  흘린다. 이미 핸들러가 직접 status를 갱신하는 기존 내장 스타일과 호환(둘 다 같은
  메서드로 수렴).
- 이후 내장 핸들러를 **천천히** registry로 이주(예: sound 계열부터). 일괄 이주는 비목표.

## 9. sync/async 디스패치 + 실행 모델 (타당성) — 리뷰 finding 1·4

`instant_actions_accept_procedure`는 **sync**(adapter_jibot.py:4030)이고 어댑터는 asyncio
루프(`self._loop`, `_run_on_adapter_loop(coro_factory)`)다. sync 컨텍스트에서 루프를 막아도,
거기서 await를 해도 안 된다. 그래서 dispatch 계약을 다음으로 못 박는다:

**`registry.dispatch(action, adapter)`는 sync wrapper다.**
- **즉시 완료되는 inline (짧은 sync)**: 핸들러를 그 자리에서 호출 → `ActionResult` →
  `_update_instant_action_status`. 기존 내장 핸들러와 동일 흐름(추가 스케줄 없음).
- **실행이 필요한 액션 (subprocess / async inline)**: dispatch가 먼저 **RUNNING을 1회
  발행**(`_update_instant_action_status(..., RUNNING)`)하고,
  `_run_on_adapter_loop(lambda: _run_action(...))`로 coroutine을 스케줄한 뒤 즉시 반환한다.
  coroutine의 **completion**에서 FINISHED/FAILED로 갱신 + `request_state_publish`.
  (manualDrive 등 기존 패턴과 동일: 먼저 상태 세팅 → `_run_on_adapter_loop`.)
  - **subprocess**: `asyncio.create_subprocess_exec` +
    `asyncio.wait_for(proc.communicate(payload), timeout)`. timeout → `proc.kill()` →
    `ActionResult(FAILED, "timeout")`. stdout 파싱 실패/non-zero exit → FAILED. stderr는 로그로.

**inline + hard timeout의 thread 위험 (finding 4)**: thread는 강제 종료가 불가하므로,
timeout 뒤에도 살아남은 thread가 `state`/`_vehicle`/`_sound`를 **나중에 mutate**할 수 있다
(예: 어댑터가 이미 다음 상태로 넘어간 뒤 vehicle 명령). 문서화만으로는 부족하므로 경계를
다음으로 좁힌다:
- **full read/write adapter access가 필요한 inline 핸들러** → **async cooperative**만 허용
  (코루틴 + `wait_for` 취소 협조) 또는 **hard timeout 없이** 둔다(행은 작성자 책임, 기존
  내장 핸들러와 동일). live adapter를 쥔 thread를 hard-kill 흉내내지 않는다.
- **thread executor 경로를 쓰는 inline** → **read-only/restricted context**로만 제한한다
  (어댑터 write 금지). 그래야 timeout 후 좀비 thread가 깨어나도 상태를 오염시키지 못한다.
- **벽시계 hard timeout(강제 종료)이 필요한 작업** → **subprocess**로 보낸다(유일하게 안전한 kill).

## 10. 에러 처리 / 격리 경계

- inline 핸들러 예외 → 디스패처가 `try/except`로 잡아 `ActionResult(FAILED, str(exc))`.
  핸들러 예외가 `instant_actions_accept_procedure` 전체를 깨지 않게 격벽.
- subprocess 비정상 종료(non-zero exit, 크래시, timeout) → `ActionResult(FAILED, ...)`.
  자식 stderr는 어댑터 로그로 캡처(진단용), stdout은 결과 JSON 전용.
- subprocess는 어댑터 상태를 못 쓰므로(§5), 잘못된 커스텀 코드가 로봇/상태를 오염시킬
  수 없다 — 최악의 경우라도 그 액션만 FAILED.

## 11. 테스트 전략

- `ActionRegistry`: 등록/조회/중복 action_type/미등록 dispatch 단위 테스트.
- `InlineRunner`: 가짜 어댑터로 status 매핑, 예외→FAILED, timeout(executor) 동작.
- `SubprocessRunner`: 가짜 자식 스크립트(고정 JSON 반환 / 비정상 종료 / hang)로
  성공·FAILED·timeout-kill·stdout 파싱 실패 경로.
- 통합: `instant_actions_accept_procedure`가 미등록 type을 registry로 위임하고 기존
  40개 내장 분기는 그대로 동작함을 회귀 테스트(기존 테스트 그린 유지).
- discovery: inline은 import 등록, subprocess는 manifest-only(코드 import 안 함) 확인.
- **리뷰 회귀 가드**:
  - (F2) 러너가 RUNNING을 terminal로 못 내게 — subprocess stdout이 RUNNING이어도
    terminal은 FINISHED|FAILED로 강제되는지(stuck 방지).
  - (F1) 실행형 액션 dispatch가 RUNNING을 먼저 발행하고 즉시 반환하며(루프 비차단),
    completion callback에서 terminal로 전이하는지(가짜 loop로 검증).
  - (F3) `_is_motion_instant_action`이 registry motion=true 커스텀 액션을 motion으로
    보고, work BUSY 중 그 액션이 게이트에서 차단되는지.
  - (F4) thread executor 경로 inline은 read-only context를 받는지(어댑터 write 금지),
    timeout 후 좀비 thread가 깨어나도 상태가 안 바뀌는지.

## 12. 영향받는 파일 (예상)

- 신규 `adaptor/core/action_registry.py` — `ActionSpec`, `ActionRegistry`,
  `ActionContext`(inline/subprocess), `ActionResult`, 러너 백엔드(`InlineRunner`,
  `SubprocessRunner`).
- 신규 `adaptor/actions/` 패키지 — 샘플 inline 액션 1개 + 샘플 subprocess 스크립트 1개(레퍼런스).
- `adaptor/config/config.py` — `[[actions]]` 파싱(기존 `instances` 패턴 미러).
- `adaptor/config/config.toml` — `[[actions]]` 예시(주석).
- `adaptor/adapter_jibot.py` — `instant_actions_accept_procedure` 마지막 `else` 직전
  registry 위임 + 디스패처가 `_update_instant_action_status`로 결과 흘리기.
- `adaptor/core/registry.py` — config `[[actions]]`의 type/motion을 WebUi
  `instant_actions`에 동적 합류.
- `adaptor/adapter_jibot.py` — `_is_motion_instant_action()`이 registry motion=true를
  함께 보도록 확장(finding 3).
- `adaptor/tests/` — §11 테스트.

## 13. 리뷰 반영 이력 (2026-06-29)

설계 리뷰 5건을 구현 계획 전에 스펙에 반영:

1. **(F1) sync/async dispatch** — `instant_actions_accept_procedure`가 sync임을 명시하고,
   `dispatch()`를 sync wrapper로 못 박음. 실행형 액션은 RUNNING 선발행 →
   `_run_on_adapter_loop` 스케줄 → completion에서 terminal. (§4, §9)
2. **(F2) RUNNING terminal 금지** — `ActionResult`/subprocess stdout의 terminal을
   FINISHED|FAILED로 제한, RUNNING은 dispatcher-start/`ctx.report()` 중간 상태로만. (§5)
3. **(F3) motion gate 배선** — `_is_motion_instant_action()`이 built-in set + registry
   motion=true를 함께 보도록 확장(busy gate가 dispatch보다 먼저 돌므로). (§8, §12)
4. **(F4) thread 위험 경계** — live full-access inline은 async cooperative timeout/무
   timeout만; thread executor 경로는 read-only context로 제한; hard-kill 필요 시 subprocess. (§2, §9)
5. **(F5) snapshot 화이트리스트 기본값** — serial_number, simulation, current_map_id,
   last_node_id, last_node_sequence_id, pose, order_id, paused, work_in_progress만 기본
   노출. credential/path/full config/raw telemetry 제외, 추가는 명시적 opt-in. (§5)

추가 메모: `ActionSpec`/`ActionResult`/`ActionContext`는 후속 order 액션 통합 재사용을
위해 instant 전용 용어 없이 중립적으로 유지. (§2)
