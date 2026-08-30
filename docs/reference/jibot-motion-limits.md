# JIBOT 주행 능력과 속도 단위 — 실측

> 측정일: 2026-08-27 · 대상: `192.168.101.50:7274` (amr2), 맵 `hana.json`
> 관련: [연속 경로 주행 설계](../superpowers/specs/2026-08-26-continuous-path-design.md)

## 왜 이 문서가 있나

`UmGetConfig` 의 속도 값들이 서로 자릿수가 다르고(`nav.limit_run_rotate_max_vel=350` vs
`robot.speed_rotate_max=90` vs 실측 `vel_r=15`), 벤더 PDF(v8, 29개 명령)에는 단위 기술이
한 줄도 없다. 어느 값을 얼마로 바꿔야 하는지 계산이 안 되어 실기로 직접 쟀다.

**한 줄 결론: `vel_f` 는 mm/s, `vel_r` 과 curve 스텝의 `w` 는 deg/s 다. 로봇은 108 deg/s 로
돌 수 있는데 goto 코너에서는 15 deg/s 만 쓴다.**

## 1. 단위 확정

### 병진 — `vel_f` 는 mm/s

`nav.limit_run_linear_max_vel = 1000` 이고, 직선 주행 실측 `vel_f` 최대가 **1002** 였다
(2026-08-26 단일 goto p40→p58). 상한을 꽉 채워 쓰므로 같은 단위다.

### 회전 — `vel_r` 은 deg/s

`UmDrive` 로 제자리 회전을 시키고 `th` 변화량과 대조했다:

| 명령 | `vel_r` 보고 | `th` 로 계산한 실제 |
|---|---|---|
| `trans=0, rot=30, speed=200` | ~54 | ~57 deg/s |
| `trans=0, rot=60, speed=200` | ~108 | ~107 deg/s |

일치한다. `vel_r` 은 deg/s 다.

### `UmDrive` 의 인자 관계

세 조합에서 일관되게 성립했다:

```
vel_r ≈ rot × speed / 111
```

| rot | speed | vel_r |
|---|---|---|
| 30 | 200 | 54 |
| 60 | 200 | 108 |
| 30 | 400 | 108 |

즉 `rot` 은 각속도 자체가 아니라 **성분/비율**이고 `speed` 와 곱해진다.
`speed=0` 이면 `rot` 을 아무리 줘도 **움직이지 않는다**(모드는 `ModeDrive` 로 바뀌지만 정지).

### `UmDrive` 는 데드맨이다

한 번 쏘고 두면 약 2.2초 뒤 `mode` 가 스스로 `Stop` 으로 돌아간다.
계속 움직이려면 재발사해야 한다(어댑터가 ~300ms 마다 보내는 이유).

## 2. 회전 능력 — 하드웨어가 아니라 플래너가 느리다

| | 각속도 |
|---|---|
| `UmDrive` 수동 조그 실측 | **108 deg/s** |
| goto 코너 실측 | **15 deg/s** |
| `robot.speed_rotate_max` 설정 | 90 ← **실측이 초과했다. 하드 캡 아님** |
| `nav.limit_run_rotate_max_vel` | 350 (안 걸림) |
| `nav.limit_goal_rotate_max_vel` | 300 (안 걸림) |

**로봇은 108 deg/s 로 돌 수 있는데 goto 는 15 deg/s 만 쓴다 — 능력의 1/7.**
80도 코너를 108 deg/s 로 돌면 0.7초인데 실제로는 **10초**가 걸린다.

이 값은 정적 설정이 아니다. `task-goto` 섹션 13개 키에 회전 속도 키가 **하나도 없다**
(전부 clearance / permit / obstacle 계열). 플래너가 정하는 값이다.

관련 있어 보이는 것 (전부 미검증):
- `nav.align_angle = 45` — 이 각도를 넘으면 먼저 제자리 정렬하는 임계로 의심. 90도 코너는 항상 초과
- `task-goto.forward_permit = False` / `backward_permit = False` — 후진을 안 허용하니 방향 전환에 항상 제자리 회전이 필요
- `plan.plan_use_radius = False`
- `nav.goal_angle_tolerance = 2` — 마지막 2도까지 맞추느라 감속 꼬리가 붙는다
  (실측 `vel_r` 이 15→14→10→8→5→4→3→2→1.6 으로 기어간다)

`task-goto.exit_no_rotate_*` 5개는 이름이 그럴듯하지만 `max_angle_err=10` /
`max_length_err=200` 이라 **10도 이내 오차 전용**으로 보인다. 90도 코너와는 무관할 것이다.

## 3. 전진하면서 회전 — `cmd:"curve"` 로 된다

라우트 스텝 `curve` 가 병진과 회전을 **동시에** 낸다. 전용 모드 `ModeCurveMove` 가 있다.

```json
{"cmd":"curve", "v":150, "w":10, "distance":600}
```

`UmSchedulerThis` 로 내린 실측 (종료용 `idle` 스텝 필수):

```
t=1.0  (13102,-2366) th= 177  vel_f=155.9  vel_r=10.08   ModeCurveMove
t=1.6  (12993,-2368) th=-175  vel_f=151.3  vel_r= 9.45   ModeCurveMove
t=2.3  (12900,-2383) th=-169  vel_f=149.7  vel_r= 8.71   ModeCurveMove
t=2.9  (12802,-2406) th=-163  vel_f=150.5  vel_r= 9.05   ModeCurveMove
```

- `v` = 병진 mm/s (명령 150 → 실측 ~150)
- `w` = 각속도 deg/s (명령 10 → 실측 ~9~10)
- `distance` = 호 길이 mm (명령 600 → 실제 이동 ~620)
- 결과: **620mm 전진하면서 39도 회전**

벤더 라우트(`routes.json`)의 실사용 예: `{"cmd":"curve","v":200,"w":10,"distance":1000}`,
`{"cmd":"curve","v":100,"w":-5,"distance":2600}` (음수 w = 반대 방향).

경로 추종 `{"cmd":"follow","path":"p2,p3,p4,p6"}` 도 존재하나 미검증이다.

## 4. `UmSetConfig` 는 먹지 않는다

`nav.align_angle` 을 45 → 46 으로 바꾸려고 `objs` 형식 7가지를 시도했으나 **전부 무시**됐다
(값 불변, 다른 설정도 불변). `ret:none` 이라 거부 사유를 알 수 없다.

시도한 형식: `{"align_angle":46}` / `{"align_angle":"46"}` / JSON 문자열 /
`[{"align_angle":46}]` / `[{"name":...,"value":...}]` / 섹션 중첩 / 섹션 중첩 문자열.

단서: 벤더 매니페스트에서 이 명령만 인자 표기가 **`"section objs"` (공백 구분)** 이고
다른 명령은 전부 쉼표다(`"routes,key,content"`, `"trans,rot,speed,lat"`). 파서가 다를 수 있다.

남은 가능성: 형식이 아직 못 맞춘 다른 모양 / `user=test` 권한 부족 / 이 빌드에서 비활성.
**설정을 런타임에 바꾸려면 벤더 확인이 필요하다.**

## 5. 미확인

- `UmSetConfig` 의 올바른 `objs` 형식
- `nav.align_angle` 을 올리면 goto 가 곡선으로 도는지 (4번 때문에 시험 불가)
- `cmd:"follow"` 가 코너를 부드럽게 도는지
- `w` 의 상한. 실측은 10 까지만 해 봤다
- 곡선 주행 중 장애물 회피가 어떻게 동작하는지 (`task-curve` 섹션에 `clearance_side_min=0` 뿐)
- 로봇마다 설정이 다르다. `192.168.101.61` 의 8/18 덤프는 `limit_run_rotate_max_vel=30`,
  amr2 는 350 이다. **한 대에서 잰 값을 다른 대에 그대로 옮기지 말 것**
