# 클램프 servo 자동 ON → 동작 → OFF 사이클

작성일: 2026-07-29
상태: 설계 승인됨
범위: 1개 리포 — `unified-amr-adaptor`(클램프 extension + EZI 설정)

## 1. 배경

현장에서 servo가 꺼진 상태로 `clamp` / `unclamp`를 실행하면 **servo만 켜지고 모터는
움직이지 않는다.** 같은 액션을 한 번 더 실행하면(이때는 servo가 이미 ON) 정상 동작한다.

원인은 `adaptor/extensions/clamp/__init__.py`의 `_enable_servo_for_motion`이다.
`servo_enable(True)`의 ACK만 확인하고 곧바로 `move_single_axis_abs_pos`를 보내는데,
EZI 드라이브는 servo가 실제로 여자되기 전에 도착한 move를 조용히 버린다. move ACK 자체는
정상(status 0)이라 어댑터는 `clamp finished`를 보고한다 — 실패로도 드러나지 않는다.

`adaptor/utils/ezi_motor.py`의 벤더 예제가 `servo_enable(True)` 뒤에 `sleep(2)`를 두고,
이동 뒤에는 `is_in_position()`을 폴링하는 것이 이 특성을 반영한다.

같은 결함이 `auto_on_auto_off` 정책도 무력화한다. `_disable_servo_after_motion`이 move
ACK 직후 `servo_enable(False)`를 보내므로 **이동 중에 전원을 끊는다.** 그래서 현재 기본값은
`auto_on_keep_on`이고, 실제로는 servo가 계속 켜진 채 남는다.

## 2. 목표 / 비목표

**목표**

- servo가 꺼져 있어도 클램프 이동 액션이 한 번에 끝까지 수행된다(ON → 이동 → OFF).
- 이동 완료 후 servo를 끄는 것이 **기본 동작**이 된다.
- servo를 계속 켜둘지 여부를 설정으로 고를 수 있다(기존 옵션 유지).

**비목표**

- 새 액션 타입 추가. 기존 10개 클램프 액션 그대로.
- `clampOn` / `clampOff` 수동 액션의 동작 변경. 그대로 둔다.
- `EziMotorClient`의 공개 API 변경. 기존 `get_axis_status()`를 그대로 쓴다.
- WebUI 패널(`panel.html`) 변경. 버튼 구성은 그대로.

## 3. 설계

### 3.1 상태 확인 기반 servo 게이트

`adaptor/extensions/clamp/__init__.py`의 `_enable_servo_for_motion` /
`_disable_servo_after_motion`을 확인 기반 헬퍼로 교체한다. 모터 상태 판정은
`get_axis_status()`가 돌려주는 `active_flags`를 쓴다.

**`_servo_on_and_wait(adapter, action_type)` → bool**

1. `get_axis_status()`로 `FFLAG_SERVOON`을 읽는다. 이미 서 있으면 `alarm_reset`도
   `servo_enable`도 보내지 않고 통과한다.
2. 꺼져 있으면 **`alarm_reset()`을 먼저 보내고** `servo_enable(True)`를 보낸 뒤,
   `FFLAG_SERVOON`이 설 때까지 `ezi_motor_poll_interval_sec` 주기로 폴링한다.
3. `clamp_servo_on_timeout_sec` 안에 서지 않으면 `RuntimeError`를 던진다 —
   **move는 보내지 않는다.**

`alarm_reset()`이 필요한 이유: 알람이 걸린 드라이브는 servo ON을 거부한다. 기본 정책이
"동작마다 다시 켠다"로 바뀌면서 래치된 알람 하나가 모든 클램프 액션을
`servo did not turn on within 3.0s`로 막고, `clampOn`도 같은 거부를 만나 in-band 복구
수단이 없어진다. `ezi_motor.py` 헤더의 벤더 예제도 `alarm_reset()` → `servo_enable(True)`
순서다. 알람이 없을 때 보내도 무해하고, 이 명령이 거부되더라도 뒤따르는 servo ON 확인이
진짜 실패를 잡으므로 결과를 따로 검사하지 않는다.

이 헬퍼의 폴링은 **유실을 봐주지 않는다.** 이동 전 servo 확인과 move ACK 확인은 단발
명령이라 무응답이 곧 고장이다(폴링 루프의 유실 허용은 아래 참조).

반환값은 "동작 후 servo를 꺼야 하는지"다 — 즉 `clamp_servo_policy == "auto_on_auto_off"`.
이번 호출에서 servo를 켰는지 여부와는 무관하다. 기본 정책은 "항상 OFF"이므로, 실행 전에
이미 켜져 있었더라도 동작 후에는 끈다(3.3 참조).

**`_wait_motion_done(adapter, action_type, *, target_position=None, success_flag=None)`**

move ACK를 확인한 직후에 호출한다. 호출자는 명령에 맞는 "도착의 증거"를 하나만 넘긴다
(절대 위치 이동이면 `target_position=`, 리미트 이동이면 `success_flag=`).

1. **시작 대기** — `FFLAG_MOTIONING`이 설 때까지 `clamp_motion_start_timeout_sec`
   동안 폴링한다. 안 서면 **도착 판정을 한 번 더 돌린다**. 확인되면 "이미 목표 위치"로
   성공, 확인되지 않으면 "드라이브가 명령을 씹은 것 같다"는 `RuntimeError`다.
2. **완료 대기** — `FFLAG_MOTIONING`이 내려갈 때까지 `clamp_motion_timeout_sec` 동안
   폴링한다. 내려가면 그것만으로 완료로 보지 않고 **도착 판정으로 확인**한다. 확인되지
   않으면 `RuntimeError`(중단된 이동)다.
3. 완료 대기가 초과되면 `move_stop()`을 먼저 보내고 `RuntimeError`를 던진다.

**도착 판정(`_motion_arrived`).** `FFLAG_MOTIONING`이 내려가는 것은 도착의 증거가 아니다.
어긋난 팔레트에 물려 드라이브가 과부하 알람(`FFLAG_ERROVERLOAD`)으로 이동을 중단해도
똑같이 내려간다. 그때 `clamp finished`를 보고하면 FMS는 클램프되지 않은 짐을 싣고 출발한다.
그래서 명령별로 증거를 따로 본다.

| 경로 | 도착의 증거 |
|---|---|
| `clamp`, `unclamp`, `clampMoveTo`, 설정 좌표를 쓰는 `clampMin`/`clampMax`/`clampHome` | `get_actual_position()`이 목표의 `clamp_position_tolerance` 안 |
| `goto_limit_minus` | `FFLAG_HWNEGALMT` |
| `goto_limit_plus` | `FFLAG_HWPOSILMT` |
| `goto_origin` | `FFLAG_ORIGINRETOK` (`_wait_origin_done`이 처리) |

`FFLAG_INPOSITION`은 도착 판정에 쓰지 않는다: move 직후에는 이 플래그가 아직 *이전* 위치
기준으로 True일 수 있어 stale True를 완료로 오판한다 — `FFLAG_ORIGINRETOK`와 같은 함정이다.
위치 비교에는 그 문제가 없다. 이번 명령이 실제로 요청한 목표와 맞대기 때문이다.

**오류 플래그.** 폴링마다 `FFLAG_EMGSTOP`이 서 있으면 즉시 `RuntimeError`다. `FFLAG_ERRORALL`은
**도착이 확인되지 않은 경우에만** 실패로 본다 — `clampMin`/`clampMax`는 하드웨어 리미트를
치는 것이 목적이라 리미트 플래그와 함께 알람이 서는 것이 정상 동작이다. 반대로 판정하면
멀쩡한 리미트 이동이 깨진다.

**유실 허용.** `EziMotorClient`는 재시도 없는 UDP 단발 교환이다. 30초 이동을 0.1초 주기로
폴링하면 ~300번을 주고받으므로 손실률 0.1%에서도 긴 이동의 약 26%가 한 번의 유실로
실패한다 — 그것도 이동 중에 모터 전원을 끊으면서. 그래서 **폴링 루프 안에서만** 연속
3번까지의 무응답·형식 오류를 봐주고(`_MotorReadError`), 정상 응답이 오면 카운터를
되돌린다. `get_actual_position()`의 무응답도 같은 예산을 쓴다. 이동 전 servo 확인과 move
ACK 확인은 그대로 엄격하다.

**예외: 원점 복귀(`clampHome`)는 `FFLAG_MOTIONING`으로 판정하지 않는다.** FASTECH 원점
복귀는 탐색 → 후퇴 → Z펄스의 다단계 시퀀스라, 단계 사이에서 `FFLAG_MOTIONING`이 잠깐
내려갈 수 있다. 그대로 재사용하면 `_wait_motion_done`이 중간 단계를 완료로 오판해서
원점 복귀 도중에 servo를 끊는다 — 이 작업이 막으려는 실패 클래스 그 자체다. 그래서
`clampHome`만 별도 헬퍼 `_wait_origin_done(adapter, action_type)`을 쓴다. 판정 플래그는
원점 복귀 전용인 `FFLAG_ORIGINRETURNING`(진행 중)과 `FFLAG_ORIGINRETOK`(성공)이다.
`FFLAG_ORIGINRETOK`는 *이전* 성공한 복귀의 값이 래치되어 남아 있을 수 있어, `goto_origin()`
직후 곧바로 읽으면 stale True를 완료로 오판할 수 있다 — `FFLAG_INPOSITION`을 완료 판정에
못 쓰는 것과 동일한 함정이다. 그래서 먼저 `FFLAG_ORIGINRETURNING`이 서는 것을 확인한
뒤에야(=이번 복귀가 실제로 시작한 뒤에야) 완료 판정을 시작하고, 완료 시점에는
`FFLAG_ORIGINRETOK`가 서 있는지까지 확인한다(안 서 있으면 `RuntimeError`).

시작 대기가 초과되면 `_wait_motion_done`과 같은 규칙을 쓴다: `FFLAG_ORIGINRETOK`로
"이미 원점"인지 확인해서 서 있으면 성공, 아니면 명령이 씹힌 것으로 보고 `RuntimeError`다.
폴링 유실도 같은 예산(연속 3회)으로 봐준다. 완료 대기 초과 시 `move_stop()` 후
`RuntimeError`(메시지는 원점 복귀를 가리킨다), `manual` 정책에서 대기 자체를 건너뛰는
것도 `_wait_motion_done`과 동일하다.

중단된 원점 복귀는 `_wait_motion_done`의 도착 판정 같은 별도 장치가 필요 없다. 복귀가
중간에 끊기면 `FFLAG_ORIGINRETURNING`이 내려간 시점에 `FFLAG_ORIGINRETOK`가 서 있지
않으므로 기존 판정이 그대로 실패로 잡는다.

`ezi_motor.py`의 `is_motion_done()`은 이름과 달리 `FFLAG_MOTIONING`(이동 **중**이면 True)을
그대로 돌려준다. 오해를 부르므로 이 헬퍼는 쓰지 않고 `get_axis_status()`를 직접 읽는다.
기존 함수는 다른 호출자가 없으므로 이번 작업에서 건드리지 않는다.

**`manual` 정책의 한계.** `_wait_motion_done`/`_wait_origin_done` 모두 `manual` 정책이면
axis status를 조회하지 않고 즉시 반환한다(3.3 참조). 그래서 `manual`에서는 action이 move
ACK 시점에 곧바로 완료로 보고되고, 이동이나 원점 복귀가 중간에 멈춰도 감지·중단되지
않는다. 의도된 동작이다 — `manual`은 servo뿐 아니라 모션도 운영자가 직접 책임지는
정책이다.

### 3.2 실행 순서

이동 액션의 흐름은 다음으로 통일한다.

```
_servo_on_and_wait()  →  move 명령 + ACK 확인  →  _wait_motion_done()
                                                 finally: 정책에 따라 servo OFF
```

servo OFF는 `finally`에 둔다. 이동이 실패하든 타임아웃이든 servo는 반드시 내려간다.
`_disable_servo_after_motion`의 기존 `finally` 배치를 그대로 유지한다.

다만 **본문 예외가 풀리는 중에 servo OFF가 실패하면 그 오류로 원래 오류를 덮지 않는다.**
덮으면 운영자가 진짜 원인 대신 엉뚱한 서브시스템(servo 통신)을 진단하게 된다. 예외가 살아
있으면 로그만 남기고 원래 오류를 그대로 올리고, 덮을 오류가 없으면(본문 정상 종료) 평소대로
실패로 올린다.

### 3.3 설정

`clamp_servo_policy`의 값 3개는 그대로 두고 **기본값만 바꾼다.**

| 값 | 동작 |
|---|---|
| `auto_on_auto_off` **(신규 기본)** | 항상 ON → 이동 → OFF |
| `auto_on_keep_on` | ON 후 켜둔 채 유지 (기존 기본) |
| `manual` | servo를 건드리지 않음 — `clampOn`/`clampOff`로만 제어 |

`manual`일 때는 `_servo_on_and_wait`이 상태 조회조차 하지 않고 즉시 통과한다. 지금 동작과
같다.

`EziConfig`에 타임아웃 3개와 도착 허용 오차 1개를 추가한다. 폴링 주기는 기존
`ezi_motor_poll_interval_sec`(기본 0.1초)를 재사용한다.

| 필드 | 기본값 | 뜻 |
|---|---|---|
| `clamp_servo_on_timeout_sec` | `3.0` | servo ON 플래그 대기 한계 |
| `clamp_motion_start_timeout_sec` | `1.0` | 이동 시작(MOTIONING) 대기 한계 — 초과하면 도착을 확인해서 성공/실패를 가른다 |
| `clamp_motion_timeout_sec` | `30.0` | 이동 완료 대기 한계. `clampTeach`의 원점 탐색 한계로도 쓴다 |
| `clamp_position_tolerance` | `500` | 절대 위치 이동의 도착 판정 허용 오차(엔코더 counts) |

`clamp_position_tolerance`는 ±16000 스트로크 기준으로 일부러 넉넉하다. 정밀도를 채점하는
값이 아니라 "도착"과 "아예 안 움직임"을 가르는 값이다.

`[ezi]` 섹션은 `config.toml`이 아니라 `extensions.hcl`이 소유하므로(`config.py:646`이
`EziConfig(**config_dict["ezi"])`로 조립한다) 설정 파일은 두 곳만 갱신한다:
`adaptor/config/extensions.hcl`, `adaptor/config/extensions.hcl.example`.

### 3.4 적용 범위

이미 `_enable_servo_for_motion`을 공유하는 6개 액션에 새 흐름을 적용한다:
`clamp`, `unclamp`, `clampMin`, `clampMax`, `clampHome`, `clampMoveTo`.

`clampTeach`도 포함한다. 원점 탐색으로 모터를 실제로 움직이는데 지금은 servo를 켜지
않아서, servo가 꺼져 있으면 똑같이 무반응이다. 다만
`initialized_open_close_encoder_position()`이 내부에 자체 폴링 루프를 갖고 있으므로
`_wait_motion_done` 없이 **`_servo_on_and_wait` → teach → 정책에 따라 OFF**만 감싼다.

그 자체 폴링 루프에는 시간 제한이 없다 — `while not await self.is_origin_sensor_on()`으로
돌고 `is_origin_sensor_on()`은 모터가 응답하지 않아도 예외 없이 `False`를 준다. teach가
servo를 켜게 된 뒤로는 원점 센서 고장이나 축 고착이 모터에 전류를 물린 채 `finally`에
닿지 못하게 만들고, order로 들어온 teach라면 order 큐까지 영영 붙잡는다. `ezi_motor.py`는
다른 호출자가 있어 손대지 않고, 클램프 extension에서 `clamp_motion_timeout_sec`로
`asyncio.wait_for`를 씌운다. 초과하면 `move_stop()`을 보내고 `RuntimeError`로 올려
`finally`가 servo를 내리게 한다.

`clampOn` / `clampOff` / `clampStop`은 그대로 둔다. servo 상태 자체를 다루는 수동 액션이다.

**`clampHome`의 `goto_origin()` 경로만 예외다.** `clampMin`/`clampMax`(리미트 이동)와
`clamp`/`unclamp`/`clampMoveTo`(절대 위치 이동)는 모두 `_wait_motion_done`을 쓰지만,
`clampHome`이 설정된 좌표 없이 `goto_origin()`으로 도는 경로는 3.1에서 설명한 다단계
시퀀스 문제 때문에 `_wait_origin_done`을 대신 쓴다. `clampHome`이 `home_position`으로
설정된 좌표로 이동하는 경우(설정 위치가 있을 때)는 일반 절대 위치 이동이므로
`_wait_motion_done`을 그대로 쓴다.

### 3.5 실패 처리

모든 실패는 `RuntimeError`로 올라가고 기존 `handle_clamp_action`이 `ActionStatus.FAILED`와
설명 문자열로 변환한다(`manual` 정책에서는 아래 표의 이동/원점 복귀 관련 행이 전혀
발동하지 않는다 — 3.1의 `manual` 정책 한계 참조).

| 상황 | 동작 |
|---|---|
| servo ON 타임아웃 | move를 보내지 않고 FAILED. `finally`에서 정책대로 OFF |
| 이동 완료 타임아웃 | `move_stop()` 후 FAILED. `finally`에서 정책대로 OFF |
| **이동이 목표 도달 전에 멈춤**(MOTIONING이 내려갔는데 도착 미확인 — 알람 중단 포함) | FAILED. `finally`에서 정책대로 OFF |
| **드라이브가 명령을 무시함**(시작 신호가 없고 도착도 미확인) | FAILED. `finally`에서 정책대로 OFF |
| **`FFLAG_EMGSTOP`** | 폴링 즉시 FAILED. `finally`에서 정책대로 OFF |
| **`FFLAG_ERRORALL`이 도착 미확인 상태로 뜸** | 폴링 즉시 FAILED. 리미트 플래그와 함께 뜬 경우는 정상 완료 |
| 원점 복귀가 ORIGINRETOK 없이 끝남(`clampHome`만 해당) | FAILED. `finally`에서 정책대로 OFF |
| **`clampTeach` 타임아웃**(`clamp_motion_timeout_sec` 초과) | `move_stop()` 후 FAILED. `finally`에서 정책대로 OFF |
| `get_axis_status()` / `get_actual_position()` 무응답(`None`) | 폴링 루프 안에서는 연속 3회까지 재시도, 초과하면 FAILED. 루프 밖(servo 확인·ACK)에서는 곧바로 FAILED |
| `finally`의 servo OFF 실패 | 본문 예외가 있으면 로그만 남기고 원래 오류를 올린다. 없으면 FAILED |

## 4. 테스트

`adaptor/tests/test_adapter_jibot_v3_order.py`의 `FakeClampMotor`에 `get_axis_status()`를
추가한다. 플래그 시퀀스를 대본처럼 주입할 수 있게 해서, 호출마다 다음 상태를 돌려주고
소진되면 마지막 상태를 유지한다. 여기에 `get_actual_position()`, `alarm_reset()`, 리미트·
오류 플래그(`limit_minus`/`limit_plus`/`error_all`/`emg_stop`), 무응답 대본 항목(`None`),
servo OFF 거부(`servo_off_comm_status`), 매달리는 teach(`teach_hangs`)를 더한다. 대본에
없는 키는 그대로 `False`라 기존 대본은 전부 그대로 돈다.

검증 항목:

- servo OFF에서 `clamp` → `alarm_reset` → `servo_enable(True)`와 ON 확인이 **move보다
  먼저**, servo OFF는 이동 완료 **뒤에** (호출 순서로 검증)
- servo가 이미 ON이면 `alarm_reset`도 `servo_enable(True)`도 보내지 않는다
- servo가 끝내 안 켜지면 move를 보내지 않고 FAILED
- 이동이 안 끝나면 `move_stop()` 후 FAILED, 그래도 servo는 OFF
- MOTIONING이 내려갔지만 목표에 못 미치면 FAILED(알람 중단 포함)
- 허용 오차 안이면 성공, `FFLAG_EMGSTOP`은 즉시 FAILED
- 리미트 이동은 리미트 플래그가 서면 `FFLAG_ERRORALL`이 같이 떠도 성공, 리미트 플래그
  없이 뜬 알람은 FAILED
- MOTIONING이 한 번도 안 서면 실제 위치로 확인해서, 이미 목표면 성공 / 아니면 FAILED
- 원점 복귀도 같은 규칙(`FFLAG_ORIGINRETOK`)으로 성공/실패를 가른다
- 폴링 중 연속 3회까지의 무응답은 견디고 4회째에 FAILED. servo 확인 루프는 한 번의
  무응답도 봐주지 않는다
- `clampTeach`가 매달리면 `clamp_motion_timeout_sec`에서 `move_stop()` 후 FAILED
- `finally`의 servo OFF 실패가 원래 오류를 덮지 않는다(본문 성공 시에는 그대로 FAILED)
- `auto_on_keep_on`은 OFF를 보내지 않는다
- `manual`은 `servo_enable`도 `alarm_reset`도 `get_axis_status`도 호출하지 않는다
- `clampTeach`가 servo ON → teach → OFF 순서로 돈다
- 기본 정책이 `auto_on_auto_off`이고 `clamp_position_tolerance` 기본이 500이다
  (`adaptor/tests/test_config.py` 갱신)

기존 `test_clamp_servo_policy_*` 3개와 `test_clamp_action_enables_servo_before_moving`은
새 흐름에 맞게 갱신한다.

## 5. 영향 범위

| 파일 | 변경 |
|---|---|
| `adaptor/extensions/clamp/__init__.py` | 헬퍼 교체, 도착 판정·유실 허용·alarm_reset·teach 타임아웃, 이동 액션 7개 흐름 변경 |
| `adaptor/config/config.py` | `clamp_servo_policy` 기본값, 타임아웃 3개 + `clamp_position_tolerance` 추가 |
| `adaptor/config/extensions.hcl` | 기본값/주석 갱신 |
| `adaptor/config/extensions.hcl.example` | 기본값/주석 갱신 |
| `adaptor/tests/test_adapter_jibot_v3_order.py` | `FakeClampMotor` 확장, 클램프 테스트 갱신·추가 |
| `adaptor/tests/test_config.py` | 기본 정책·허용 오차 기대값 갱신 |

**운영 영향.** 배포된 로봇의 `extensions.hcl`에 `clamp_servo_policy`가 명시되어 있으면 그
값이 이긴다. 명시가 없는 로봇만 `auto_on_auto_off`로 바뀐다. 리포의
`adaptor/config/extensions.hcl`이 `auto_on_keep_on`을 명시하고 있으므로 함께 갱신해야 새
기본값이 실제로 적용된다. 리포 루트의 `config.toml` / `extensions.hcl`은 git에 없는 로컬
실행용 파일이므로 이번 작업에서 건드리지 않는다 — 필요하면 운영자가 직접 고친다.

클램프 이동 액션이 **이동 완료까지 블로킹**으로 바뀐다(`manual` 정책은 예외 — 아래 참조).
지금은 move ACK 직후 FINISHED를 보고하지만, 이후에는 실제 완료 시점에 보고한다. instant
action은 각각 별도 asyncio 태스크로 돌고(`_schedule_on_adapter_loop`) `EziMotorClient`의
UDP 송수신은 `run_in_executor`로 빠지므로, 대기 중에 event loop나 다른 액션 처리가 막히지는
않는다.

**FINISHED의 뜻이 달라진다.** 이전에는 "move ACK를 받았다"였고 이제는 "목표에 도착한 것을
확인했다"다. 그래서 예전에는 조용히 성공으로 넘어가던 상황(중단된 이동, 씹힌 명령, 이미
목표 위치가 아닌데 시작 신호가 없는 경우)이 이제 FAILED로 드러난다. 설비 정렬이나 위치
설정이 어긋난 로봇에서는 배포 직후 실패가 늘어 보일 수 있는데, 이는 새 결함이 아니라
그동안 가려져 있던 실패다. 도착 판정이 지나치게 빡빡해 보이면 `clamp_position_tolerance`로
조정한다.

**폴링 부하.** 절대 위치 이동은 이동이 끝난 폴링에서만 `get_actual_position()`을 한 번 더
쓴다(알람이 뜬 폴링에서도 한 번). 이동 중 폴링은 여전히 `get_axis_status()` 한 번이다.

`clamp_servo_policy`가 `manual`이면 위 블로킹이 적용되지 않는다. `_wait_motion_done` /
`_wait_origin_done` 모두 `manual`에서 axis status 조회 자체를 건너뛰므로, action은 move
ACK 시점에 곧바로 FINISHED로 보고되고 실제 이동/원점 복귀가 멈춰도 감지되지 않는다(3.1
참조). `manual`을 고르는 순간 모션 감시도 운영자 책임이 된다.

**알려진 한계.** `EziMotorClient`는 UDP 소켓 하나를 공유하고 요청/응답을 sync_no로 짝짓지
않는다. 클램프 액션 두 개가 겹쳐 실행되면 응답이 엇갈릴 수 있다. 기존에도 있던 제약이고
(모터를 쓰는 곳은 클램프 extension뿐이다) 이번 작업에서 해소하지 않는다. 다만 폴링이
들어가면서 노출 시간이 길어지므로 기록해 둔다.
