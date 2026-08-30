# JIBOT 범퍼 정지 에러 표면화 + `#disable` 토큰 처리

- 날짜: 2026-08-12
- 상태: 설계 승인됨 (구현 대기)
- 범위: `unified-amr-adaptor` (어댑터). eq(`fabris-equipments`) 대응은 별도 작업
- 선행 문서: `2026-06-25-jibot-stop-reason-subdivision-design.md`

> 라인 번호는 2026-08-12 20:22 시점 `adapter_jibot.py` 기준. 조사 도중 무관한 변경
> (`OrderStep.actions_only`, `_status_goal_pose`)이 들어와 이전 초안 대비 +5 밀렸다.

## 1. 배경 / 문제

192.168.101.62(HN-SH6-TR-002)에서 범퍼가 눌려 로봇이 멈췄으나 어댑터가 이를 **에러로 보고하지
않았다.**

로봇 자체 기록(`/usr/local/urobot/logs/bot_log/st_2026-08-12__10-59-36.txt`, `/store_motor`
노드가 `/jrobot_status`를 받아 기록)에서 확인한 실제 이벤트:

```
Aug-12-11:21:58  bumper Trigger!       BUMP_ESTOP=1 HMI_ESTOP=0 MOTOR_ERR=0 PC_ESTOP=0 MOTOR_EN=1
Aug-12-11:21:59  bumper Trigger!       BUMP_ESTOP=1 HMI_ESTOP=0 MOTOR_ERR=0 PC_ESTOP=0 MOTOR_EN=0
...                                    (919행 동일 조합)
Aug-12-11:29:38  Press ON to Enable.   BUMP_ESTOP=1 MOTOR_EN=0   (범퍼 해제, 재활성 대기)
Aug-12-11:29:43  Normal...             BUMP_ESTOP=0 MOTOR_EN=1   (복구)
```

**11:21:58 ~ 11:29:38, 7분 40초**간 지속. 당시 로봇은 오더 수행 중이었다
(`MRosGoto` → pose 13870,-2541). 2026-08-11에도 3회 발생(11:44/12:44/13:44 파일).

### 1.1 파이프라인은 정상 — 분류도 정상

조사 중 배제한 가설들:

- ROS 리스너 다운? → 아님. `rostopic echo -p /jrobot_status` 자식 프로세스(pid 2739)가
  10:59:44부터 어댑터(pid 2453) 아래에 생존.
- 배포 코드 구버전? → 아님. 배포본 `adapter_jibot.py`에 BUMPER 분류가 로컬과 동일하게 존재.
- 분류 실패? → 아님. 위 실측 필드값 그대로 재현한 결과 `reason == "BUMPER"`.

재현 결과(실측값 입력):

```
reason          = BUMPER          ← 분류는 정확
field_violation = True
driving         = False
errors          = []              ← 범퍼 관련 에러 없음
```

### 1.2 근본 원인

**BUMPER는 설계상 무음(silent) 정지 사유다.** `MOTOR_FAULT`는 FATAL `JIBOT_MOTOR_FAULT`를
올리지만, BUMPER는 플래그 두 개만 세팅하고 끝난다.

- `ErrorType` enum에 `JIBOT_BUMPER`가 **아예 없음** (`vda5050_2_0_0_state.py:47-55`)
- `_apply_jibot_stop_reason()`(2300)의 범퍼 분기(2323)가 `field_violation`/`driving`만 세팅.
  이어지는 `_refresh_jibot_motor_fault_errors(reason == "MOTOR_FAULT")`는 범퍼일 때 `False`
- 로그도 없음 — 어댑터 journal **전체 기록에서 `bumper` 0건** (당일만 9166줄이 쌓였는데도)

결과적으로 ACS/운영자에게는 `fieldViolation=true` + `driving=false`(→ workingState `BLOCKED`,
detail `BRAKE`)로만 보여 **평범한 장애물 대기와 구분되지 않는다.**

### 1.3 부수 발견 — `#disable` 토큰 미처리

범퍼 지속 중 7273 `UmGetCurTask`/`UmGetTaskInfo`의 status는
`nrunto pose (13870 -2541 0)#disable#slowdown`이었다. 어댑터는 `#brake`/`#lost`/`#slowdown`/
`#watch`만 파싱하므로(1077, 1099, 1158-1161) `#disable`를 버리고 `JIBOT_AVOIDANCE("slowdown")`
WARNING만 올렸다 — 두 번째 독립 신호마저 놓쳤고, 실제로는 모터가 꺼져 선 것을 "장애물 회피 중"으로
보고했다.

### 1.4 부수 발견 — `bumpe_stop` 폴라리티 미해결 항목 해소

선행 문서 §7.2가 남긴 미해결 항목("범퍼 0↔1 토글로 폴라리티 확인 권장")을 전체 이력으로 해소했다.
`BUMP_ESTOP=1`인 행의 `system_status` 분포:

| system_status | 행 수 |
|---|---|
| `Press ON to Enable.` | 31,389 |
| `Estop Pressed!` | 15,958 |
| `bumper Trigger!` | 965 |

**`bumpe_stop`은 범퍼 전용 신호가 아니라 e-stop 체인 전반에 걸린다.** 따라서 텍스트 기반 분류는
옳은 판단이었고, 이 플래그는 계속 불신 대상으로 두고 원시값 전달만 한다. 선행 문서 §7.2는
"확인 후 신뢰"가 아니라 **"확인 결과 신뢰 불가"로 종결**한다.

## 2. 목표

1. 범퍼 정지를 `errors[]`에 **FATAL `JIBOT_BUMPER`**로 표면화하고, 해제 시 자동 소멸시킨다.
2. **오더는 절대 중단되지 않는다.** 범퍼는 에러 상태로만 드러나고, 해제되면 진행 중이던 오더가
   그대로 이어져야 한다 (§3.5가 이 요구를 코드로 보장한다).
3. `#disable` 토큰을 인식해 ROS 리스너와 **독립된 2차 감지 경로**를 확보한다.
4. stop reason 전이를 로그로 남겨 사후 조사가 journal만으로 가능하게 한다.

### 2.1 비목표

- eq 쪽 렌더링/상태 룰 변경 (별도 작업, §7)
- `bumpe_stop` 플래그를 분류에 사용하기 (§1.4에서 신뢰 불가로 결론)
- 범퍼 지속 시간에 따른 에러 레벨 승격 (YAGNI — 필요해지면 그때)

## 3. 설계

### 3.1 `JIBOT_BUMPER` FATAL 에러

`vda5050_2_0_0_state.py`의 `ErrorType`에 추가:

```python
JIBOT_BUMPER = "JIBOT_BUMPER"  # Bumper contact stop reported via /jrobot_status ("bumper Trigger!")
```

`_refresh_jibot_motor_fault_errors`(2282)와 **동일한 purge-then-append 패턴**으로 신설한다.
매 사이클 제거 후 조건부 추가라 set/clear가 자동으로 맞는다:

```python
def _refresh_jibot_bumper_errors(self, active):
    """Set/clear a FATAL JIBOT_BUMPER state error each cycle."""
    if self.state is None:
        return
    self.state.errors = [
        e for e in self.state.errors
        if getattr(e, "error_type", None) != ErrorType.JIBOT_BUMPER
    ]
    if active:
        self.state.errors.append(
            Error(
                error_type=ErrorType.JIBOT_BUMPER,
                error_level=ErrorLevel.FATAL,
                error_references=[ErrorReference("reason", "bumperTrigger")],
                error_description="JIBOT reported a bumper contact stop (bumper Trigger!)",
            )
        )
```

`_apply_jibot_stop_reason()`(2300)에서 호출한다:

```python
if reason is None:  # legacy fallback (no subdivision data)
    self.state.safety_state.e_stop = (...)
    self._refresh_jibot_motor_fault_errors(False)
    self._refresh_jibot_bumper_errors(False)   # ← 필수. 아래 주의 참고
    return
...
self._refresh_jibot_motor_fault_errors(reason == "MOTOR_FAULT")
self._refresh_jibot_bumper_errors(reason == "BUMPER")
```

> **주의 (놓치기 쉬운 지점).** legacy fallback 분기에서 반드시 `False`로 호출해야 한다. 범퍼가
> 눌린 상태에서 ROS 리스너가 죽으면 `reason`이 `None`이 되어 이 분기로 빠지는데, 여기서 정리하지
> 않으면 **에러가 영구 래치된다.** 기존 `_refresh_jibot_motor_fault_errors`도 같은 이유로 이
> 분기에서 `False`를 호출하고 있다.

### 3.2 FATAL을 고른 이유 — 그것이 곧 "에러 상태"다

`_derive_amr_working_state`(1236):

```
1287:  if has_fatal or estopped:              working_state = "ERROR"
1289:  elif field_violation and not driving:  working_state = "BLOCKED"

1273:  elif has_fatal:                        detail = "FAULT"
1275:  elif field_violation and not driving:  detail = "BRAKE"
```

`has_fatal`(1261)이 `field_violation` 조건보다 위에 있으므로 FATAL 범퍼 에러가 붙으면
**workingState가 `BLOCKED` → `ERROR`, detail이 `BRAKE` → `FAULT`로 바뀐다.**

즉 요구사항인 "에러 상태"를 만드는 것이 정확히 이 FATAL이다. WARNING으로 낮추면 workingState는
`BLOCKED`에 머물러 에러 상태가 되지 않는다. **FATAL 유지가 맞다.**

**호출 순서 확인함:** `_refresh_amr_state_information()`(709)이 `_apply_jibot_stop_reason()`(680)
보다 뒤에 실행되므로 FATAL 에러가 **같은 사이클에** 반영된다. 한 사이클 지연은 없다.

어댑터 안에서 `ErrorLevel.FATAL`(정의: `protocol/vda5050_common.py:9`)을 소비하는 곳은 이
`has_fatal` 한 곳뿐이다(전수 확인). 나머지는 전부 에러 *생성*이다.

### 3.3 오더는 중단되지 않는다 — 어댑터 측 근거

- **어댑터는 FATAL을 보고 오더를 취소하지 않는다.** FATAL 소비처가 §3.2의 `has_fatal`
  한 곳뿐이고, 그건 표시용 상태 계산에만 쓰인다. 오더 취소 경로는 ACS가 보내는 명시적
  `cancelOrder` instant action뿐이다.
- **노드 대기에는 타임아웃이 없다** (4776-4778: *"The wait has no timeout by design: a robot
  blocked by an obstacle (brake) legitimately waits here, and cancelOrder or a new order cancels
  the worker task."*). 범퍼로 멈춰도 오더 워커는 그 자리에서 계속 기다리다가 해제되면 이어간다.
- **선례가 이미 있다** — `JIBOT_CONNECTION_LOST`, `JIBOT_LOCALIZATION_LOST` 둘 다 FATAL이면서
  조건 해소 시 자동 소멸하는 일시적 오류다. 범퍼도 같은 성격이며 새로운 패턴이 아니다.

단, 아래 §3.5의 stall 감지기만은 예외이므로 반드시 함께 고쳐야 한다.

### 3.4 `#disable` — ROS 독립 감지 경로

`_is_jibot_lost`(1099)와 동일한 형태의 헬퍼를 추가한다:

```python
def _is_jibot_motor_disabled(self) -> bool:
    """True when JIBOT's own task status reports the motor disabled ("#disable").

    Arrives over TCP 7273, so this still works when the ROS listener is down and
    the /jrobot_status stop-reason subdivision is unavailable.
    """
    return self._jibot_text_contains("#disable", " disable")
```

`_refresh_jibot_status_errors()`(2437)의 purge 목록에 `JIBOT_MOTOR_DISABLED`를 추가하고,
`_build_jibot_status_error`(2488, 기본 레벨 WARNING)로 **WARNING** 에러를 발행한다.

**WARNING인 이유:** `#disable`는 범퍼 전용이 아니라 "모터가 꺼짐"만 뜻하며 평범한 수동
disable에서도 뜬다. FATAL은 텍스트로 확정된 범퍼(§3.1)에만 주고, `#disable`는 거기까지만
말하게 하는 것이 정직하다. 리스너가 죽어 §3.1이 동작하지 않을 때에도 최소한의 정지 사유는 남는다.

기존 `JIBOT_AVOIDANCE` 발행은 **그대로 둔다.** `#slowdown`이 실제로 함께 떠 있었으므로 보고 자체가
거짓은 아니고, 이번 변경의 목적은 신호 추가이지 기존 경로 수정이 아니다.

### 3.5 stall 감지기에 범퍼 면제 추가 — **오더 보호의 핵심**

`_wait_for_node`의 stall 판정(4846-4852):

```python
stalled = (
    self._is_jibot_stopped()
    and not self._is_jibot_obstacle_wait()   # #brake      → 면제
    and not self._is_jibot_manual_drive()    # 조이스틱     → 면제
    and not self._manual_control_active      # 어댑터 조그  → 면제
    and not self._motion_paused              # stopPause   → 면제
)
```

주석은 *"an obstacle brake, a hand on the joystick and a stopPause are all legitimate reasons to
sit here, and none of them are ours to interrupt"* 라고 말한다. **범퍼 정지도 정확히 그런
사유인데 면제 목록에 없다.**

면제되지 않으면 stall로 판정되어:

- `unreached_delay`(기본 1.0초) 후 `JIBOT_NODE_UNREACHED` 에러 보고 (4863)
- `retry_delay`(기본 3.0초)마다 goto 재전송, `retry_limit`(기본 3회)까지 (4882)
- 재시도 소진 시 `gave_up` → FATAL `JIBOT_NODE_UNREACHED` 확정 (4893)

즉 **범퍼가 눌린 채로 몇 초만 지나면 goto 재전송 3회를 태우고 노드 도달 실패로 확정된다.**
이것이 §2-2 "오더 중단 금지"를 어댑터 안에서 깨뜨릴 수 있는 유일한 경로다.

**이번 이벤트에서는 우연히 걸리지 않았다.** `_is_jibot_stopped()`(1151)는 status의 *완전 일치*
(`== "stop"`/`"stopped"` 또는 설정값)를 요구하는데, 당시 status는
`nrunto pose (13870 -2541 0)#disable#slowdown`이라 불일치했다. 하지만 이건 문자열이 우연히
어긋난 덕분이지 설계된 보호가 아니다. JIBOT이 범퍼 중 status를 `stop`으로 바꾸는 조건이 하나라도
있으면 바로 터진다.

**수정:** 면제 목록에 범퍼/모터 disable을 추가한다.

```python
stalled = (
    self._is_jibot_stopped()
    and not self._is_jibot_obstacle_wait()
    and not self._is_jibot_manual_drive()
    and not self._manual_control_active
    and not self._motion_paused
    and not self._is_jibot_motor_disabled()                    # #disable (7273)
    and getattr(self, "_jibot_stop_reason", None) != "BUMPER"  # /jrobot_status
)
```

두 조건을 모두 넣는 이유는 §3.4와 같다 — 7273 경로와 ROS 경로 중 하나가 죽어도 나머지가 오더를
지킨다. 주석도 함께 갱신해 범퍼가 면제 사유임을 명시한다.

## 4. 데이터 흐름 요약

```
ROS /jrobot_status ──> bms_ros_listener ──> vehicle._robot_safety
                                                  │
                                    _derive_jibot_stop_reason()
                                                  │  "bumper Trigger!" → BUMPER
                                                  ▼
                                    _apply_jibot_stop_reason()
                                       ├─ field_violation = True
                                       ├─ driving = False
                                       ├─ JIBOT_BUMPER (FATAL)        ← 신규
                                       └─ [JIBOT STOP REASON] 로그     ← 신규
                                                  │
                    _refresh_amr_state_information() → workingState=ERROR, detail=FAULT
                                                  │
                              _wait_for_node stall 면제 ← 신규 (오더 유지)

TCP 7273 UmGetCurTask.status ──> vehicle._status
                                       ├─ "#disable" → JIBOT_MOTOR_DISABLED (WARNING)  ← 신규
                                       └─ "#disable" → stall 면제                       ← 신규
                                          (ROS 리스너 다운 시에도 동작)
```

## 5. stop reason 전이 로그

`_apply_jibot_stop_reason()`에서 `self._jibot_stop_reason`을 갱신하기 **전에** 이전 값과 비교해
바뀔 때만 한 줄 남긴다. publish 루프가 ~3Hz라 매 사이클 출력하면 스팸이 된다.

```python
prev = self._jibot_stop_reason
if reason != prev:
    print(f"[JIBOT STOP REASON] {prev} -> {reason}")
self._jibot_stop_reason = reason
```

이번 조사에서 journal에 `bumper`가 0건이라 로봇 자체 기록까지 뒤져야 시각을 특정할 수 있었다.

## 6. 테스트 (TDD)

픽스처는 §1의 62 실측 조합을 그대로 쓴다:
`{"system_status": "bumper Trigger!", "bumpe_stop": "1", "hmi_estop": "0", "motor_error": "0",
"pc_estop": "0", "motor_enable": "0", "pc_enable": "1"}`

- `_refresh_jibot_bumper_errors`: 범퍼 시 FATAL `JIBOT_BUMPER` 존재 → `NONE` 복귀 시 소멸
- **래치 회귀 방지**: 범퍼 활성 상태에서 safety가 stale/absent로 전환되면(리스너 다운 모사)
  래치된 범퍼 에러가 제거되는지 (§3.1 주의 항목)
- **상태 회귀 고정**: 범퍼 시 `_derive_amr_working_state()`가 `("ERROR", "FAULT")`
  (§3.2에서 고른 의미를 잠근다)
- **오더 보호 (§3.5, 가장 중요)**: 범퍼로 정지 + `_is_jibot_stopped()`가 True인 상황에서
  goto 재전송이 일어나지 않고 `JIBOT_NODE_UNREACHED`로 확정되지 않는지. `#disable` 경로와
  `_jibot_stop_reason == "BUMPER"` 경로를 **각각 단독으로** 검증한다(한쪽이 죽어도 지켜지는지).
- `MOTOR_FAULT`가 범퍼보다 우선일 때 두 에러가 동시에 뜨지 않고 사유대로 갈리는지
- `_is_jibot_motor_disabled`: `#disable` 포함 시 True, `#slowdown`만 있을 때 False
- `JIBOT_MOTOR_DISABLED` WARNING set/clear
- stop reason 전이 로그가 **변화 시에만** 출력되는지 (동일 사유 반복 시 무출력)

기존 23개 테스트(`test_jibot_stop_reason`, `test_jibot_stop_reason_apply`,
`test_jibot_safety_info`)는 계속 통과해야 한다. 단 detail/workingState 관련 기존 기대값이 있다면
§3.2에 맞춰 갱신한다.

## 7. 크로스레포 계약 / 배포

- `JIBOT_BUMPER`, `JIBOT_MOTOR_DISABLED`는 신규 errorType 토큰이다. eq는 모르는 errorType을
  무시하므로 **어댑터 단독 배포는 안전하다.**
- 다만 §3.2의 workingState `BLOCKED` → `ERROR` 는 **eq/ACS에 보이는 동작 변경**이다.
  어댑터는 오더를 유지하지만(§3.3, §3.5), **ACS가 workingState=ERROR를 보고 스스로 오더를
  취소하는 정책이라면 그건 ACS 쪽에서 막아야 한다.** 배포 전 반드시 공유할 것 — 이것이 "오더를
  중단시키면 안 된다"는 요구가 어댑터 밖에서 깨질 수 있는 유일한 지점이다.
- eq에서 `JIBOT_BUMPER`를 표시/대응하도록 하는 작업은 별도 티켓.

## 8. 위험 / 미해결

1. **ACS 정책 의존** — §7의 두 번째 항목. 어댑터가 보장할 수 있는 범위 밖이다.
2. **범퍼 접촉이 잦으면 ERROR가 잦아진다.** 당일 1회(7분 40초), 08-11에 3회 관측. FATAL 선택은
   사용자 결정이며, 운영상 과하면 §2.1의 지속시간 승격 방식으로 전환할 여지를 남긴다.
3. **해제 직후 5초 공백** — 11:29:38에 `BUMP_ESTOP=1`인 채 status가 `Press ON to Enable.`로
   바뀌어 reason이 `MANUAL`이 되고(→ `operatingMode=MANUAL`), 11:29:43에 `Normal...`로 복구된다.
   이 5초간 범퍼 에러는 이미 해제된다. 물리적으로는 범퍼가 풀린 뒤 재활성 대기 구간이므로 의도된
   동작으로 본다.
4. **`#disable` 동반 토큰 미확정** — 범퍼 외 상황에서 `#disable`가 어떤 조합으로 뜨는지 전수
   확인하지 않았다. WARNING으로 두어 오탐 비용을 낮췄다. 다만 §3.5의 stall 면제에도 쓰이므로,
   `#disable`가 과하게 뜨면 stall 감지가 둔해질 수 있다 — 배포 후 관찰 대상.
5. **미검증 항목** — 범퍼 래치 중 어댑터가 `UmDrive`를 계속 송신했는지 확인하려던 중 로봇이
   네트워크에서 떨어져(`No route to host`) 세지 못했다. 11:53 시점엔 `UmDrive` 스트리밍 중이었다.
   §3.5 수정 후 재확인할 것.
