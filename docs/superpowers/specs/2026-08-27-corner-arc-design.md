# 코너 출발 호 (Corner Arc) — 설계 문서

- 날짜: 2026-08-27
- 상태: **설계 확정, 미구현.** 구현 계획서는 `docs/superpowers/plans/`에 별도.
- 목표: 90도 코너에서 로봇이 **제자리 회전으로 10초를 쓰는 것**을 없앤다. 다음 노드로
  출발할 때 짧은 호를 그려 **돌면서 나간다.**
- 확정 사항: 라우트 스텝 `cmd:"curve"` 를 쓴다. `UmDrive` 는 쓰지 않는다(§3.2).
  호는 **출발**에만 쓰고 코너를 깎지 않는다. goto 는 그대로 남긴다.
- 기본값 **off**. `[settings] corner_arc_enabled` 로 켠다.
- 실측 근거: [JIBOT 주행 능력과 속도 단위](../../reference/jibot-motion-limits.md)

---

## 1. 지금 무엇이 문제인가

2026-08-26 실측(단일 goto `p40 → p58`, 코너 4곳, 총 91초)에서 **정지 51초(56%)가 코너**였다.
그리고 그 시간은 대기가 아니라 **전부 제자리 회전**이다:

| 코너 | 총 | 회전 중 | 완전 정지 | 각도 |
|---|---|---|---|---|
| 2번째 | 9.7 s | **9.7 s** | 0 s | 79도 |
| 3번째 | 10.3 s | **10.3 s** | 0 s | 83도 |
| 4번째 | 9.8 s | **9.8 s** | 0 s | 81도 |

**80도 도는 데 10초 = 평균 8 deg/s.** 그런데 같은 로봇이 `UmDrive` 수동 조그에서는
**108 deg/s** 를 냈다(2026-08-27 실측). 능력의 1/7만 쓰고 있다.

이 속도는 정적 설정이 아니다. `task-goto` 섹션 13개 키에 회전 속도 키가 **하나도 없고**
(전부 clearance / permit / obstacle 계열), `nav.limit_run_rotate_max_vel = 350` 과
`robot.speed_rotate_max = 90` 은 둘 다 걸리지 않는다(실측 108이 후자를 넘었다).
**플래너가 스스로 느리게 도는 것이다.**

`nav.align_angle = 45`(이 각도를 넘으면 먼저 제자리 정렬하는 임계로 의심)를 올려 보려 했으나
`UmSetConfig` 가 `objs` 형식 7가지 모두에서 무시됐다(§3.3). **설정으로는 못 고친다.**

## 2. 무엇을 만드는가

`goto(N+1)` 을 보내기 **직전에**, 돌아야 할 각이 크면 짧은 호를 먼저 그린다.

```
지금:   [goto N+1] -> 로봇이 제자리 회전 10초 -> 전진
바뀜:   [curve 로 돌면서 전진 1.6초] -> [goto N+1] -> 남은 거리 주행
```

호가 끝나면 로봇은 이미 N+1 쪽을 보고 있으므로 뒤이은 goto 는 제자리 회전 없이 출발한다.

**goto 를 제거하지 않는 것이 이 설계의 핵심 안전장치다.** `curve` 는 개루프(목표 좌표가
없고 시킨 호를 그릴 뿐, 빗나가도 스스로 안 고친다)이므로, 매 구간 끝에 폐루프 goto 가
붙어야 오차가 누적되지 않는다.

## 3. 왜 이 방식인가 — 검토한 대안

### 3.1 코너 전부터 미리 돌기 (기각)

가장 매끄럽지만 **코너를 깎는다.** 엣지를 벗어나므로 FMS 점유 관제 위반이다.
[연속 경로 주행 설계](2026-08-26-continuous-path-design.md) §7이 이미 "코리도어 지름길은
범위 밖"으로 정했다. 같은 이유로 여기서도 기각한다.

### 3.2 `UmDrive` 로 빠른 제자리 회전 (기각 — order 를 깬다)

곡선 없이 회전만 빠르게 하는 안이다. 이득이 거의 같고(1.5초 vs 1.6초) 개루프 병진이 없어
한때 이쪽을 추천했으나, **`operatingMode` 를 깨뜨린다.**

`UmDrive` 를 쓰면 로봇이 `_mode = "ModeDrive"` 를 보고한다. 그런데
`config.toml:116` 이 `manual_modes = ["ModeDrive"]` 이고,
`_derive_operating_mode`(`adapter_jibot.py:1480`)가 `_is_jibot_manual_drive()`(`:1514`)일 때
**`OperatingMode.MANUAL` 을 발행한다.**

즉 자율 오더 수행 중 **코너마다 1.5초씩 "사람이 운전 중"이라고 FMS 에 보고**하게 된다.
VDA5050 에서 MANUAL 은 관제가 제어하지 않는다는 뜻이므로 FMS 가 오더를 거두거나 알람을 낼 수 있다.

살리려면 "사람의 조그"와 "어댑터가 오더 중 낸 회전"을 구분하는 새 개념을 만들어
`_derive_operating_mode` / 스톨 가드(`:245`, `:6169`) / idle 캡처에 전부 배선해야 한다.
작은 변경이 아니다.

**`curve` 는 이 문제가 없다** — 실측에서 모드가 `ModeCurveMove` 였고 `manual_modes` 에 없다.
`operatingMode` 는 AUTOMATIC 을 유지한다.

### 3.3 `UmSetConfig` 로 플래너 설정 바꾸기 (막힘)

`nav.align_angle` 을 45 -> 46 으로 바꾸려고 `objs` 형식 7가지를 시도했으나 **전부 무시**됐다
(값 불변, 다른 설정도 불변). `ret:none` 이라 거부 사유를 알 수 없다.
남은 가능성은 형식 미상 / `user=test` 권한 부족 / 이 빌드에서 비활성이며, **벤더 확인이 필요하다.**
이 경로가 열리면 이 설계보다 훨씬 싸게 끝날 수 있으므로 별도로 추적한다.

## 4. 호 기하

```
Δθ    = 방위(현재위치 -> N+1) - 현재 heading          [-180, 180) 로 접는다
R     = corner_arc_radius_mm
v     = corner_arc_speed_mm_s
w     = degrees(v / R),  부호는 sign(Δθ)              [deg/s]
호길이 = R * |Δθ(rad)|                                 [mm]
```

`v=300, R=300` 이면 `w=57 deg/s`, 90도에 **1.6초 / 470 mm**.

실측으로 확인된 대응(§`jibot-motion-limits.md` 3절):
`v` = 병진 mm/s, `w` = 각속도 deg/s, `distance` = 호 길이 mm. 명령값과 실측이 1:1이다.

**대가**: 호가 끝나면 로봇이 N->N+1 직선에서 **반경만큼 옆으로** 벗어난다.
90도 좌회전이면 시작점 기준 (R, R) 지점에 서고, 출발선 기준 R 만큼 바깥이다.
뒤이은 goto 가 보정한다. R 을 키우면 부드럽지만 더 벗어나고 줄이면 반대다.

**R 의 현장 제약은 통로 폭이다.** 기본 300 mm 는 로봇 반경(`robot.size_radius = 613`)보다
작지만, 좁은 통로에서는 줄여야 한다. 현장 값은 운영자가 정한다.

## 5. 호를 쓰지 않는 조건

하나라도 걸리면 오늘 동작 그대로 간다. 판정은 `goto` 발신 직전에 한 번만 한다.

| 조건 | 근거 |
|---|---|
| `corner_arc_enabled = false` | 기본값 |
| `|Δθ|` < `corner_arc_min_angle_deg` (기본 30) | 돌 게 없으면 이득이 없다 |
| `|Δθ|` > 150 | 거의 반전. 호가 크게 휘어 경로를 벗어난다 |
| N+1 까지 거리 < 호 길이 x 2 | 호만으로 도착해 버려 goto 가 보정할 여지가 없다 |
| dock 세그먼트 룰 매칭 | `_dock_segment_rule`(`:5433`) — UmDock 경로다 |
| move 룰 매칭 | `_move_motion_rule`(`:5408`) — 상대이동은 시작 pose 를 먼저 읽는다 |
| dock work 노드 | `_is_dock_work_node`(`:5481`) |
| 좌표 또는 heading 미상 | `_resolve_node_target`(`:5904`) 이 None, 또는 `_vehicle_xy`(`:4967`) 가 None |

**연속 경로 주행(CP)과는 독립이다.** CP 는 *도착* 판정을 바꾸고 이 설계는 *출발* 을 바꾼다.
둘 다 켜도 순서는 `호 -> goto -> 도착 대기(통과 또는 정지)` 로 겹치지 않는다.

## 6. 완료 판정과 실패 처리

`curve` 는 `UmSchedulerThis`(`jibot-client/client.py:1022`)로 나가고 **응답이 없다**(`ret:none`).
그래서 완료를 관측으로 판정한다.

- 시작: `mode` 가 `ModeCurveMove` 로 바뀐다
- 완료: `ModeCurveMove` 를 벗어난다
- 라우트는 `{"a": curve, "a1": {"cmd":"idle"}}` 로 **반드시 종료 스텝을 붙인다** —
  벤더 라우트 `AB_1000`/`EF` 는 마지막이 `jump` 로 첫 스텝에 되돌아가는 무한 루프다
  (CP 설계 §3.3에서 확인)

**타임아웃(`corner_arc_timeout_sec`, 기본 10초)이 나면 `um_stop()`(`client.py:1091`) 후
그냥 goto 로 떨어진다.** 호가 실패해도 오더는 오늘과 똑같이 진행된다.
호는 최적화이지 필수 경로가 아니다 — 이것이 이 설계의 안전망이다.

## 7. 설정

```toml
[settings]
# 코너 출발 호. 다음 노드로 나갈 때 제자리 회전 대신 짧은 호를 그린다.
corner_arc_enabled       = false
corner_arc_radius_mm     = 300     # 호 반경. 클수록 부드럽지만 경로를 더 벗어난다
corner_arc_speed_mm_s    = 300     # 호 병진 속도
corner_arc_min_angle_deg = 30      # 이 각도 미만이면 호를 쓰지 않는다
corner_arc_timeout_sec   = 10.0    # 초과하면 UmStop 후 goto 로 폴백
```

- `adaptor/config/config.toml:46` `[settings]` 와 `adaptor/config/config.py:194` `Settings`
  **양쪽에 같은 이름**을 넣어야 한다. dataclass 필드 없이 toml 키만 넣으면 `_section()` 이
  `ConfigError` 를 던져 **부팅이 실패한다**.
- 이름에 `mode` 라는 단어를 쓰지 않는다. 이 저장소에서 이미 6가지 다른 뜻으로 쓰인다.
- 배포가 `--config-toml-mode keep` 기본이라 새 키는 현장 로봇에 도달하지 않는다.
  **dataclass 기본값이 곧 현장 동작이다.**

## 8. 위험

| 위험 | 대응 |
|---|---|
| **곡선 주행 중 장애물 거동 미확인** | `task-curve` 섹션에 안전 키가 `clearance_side_min = 0` 하나뿐이다. `task-goto` 는 13개(`clearance_front_init`, `front_obs_warn_dist`, `lost_stop`, `stuck_move_dist` 등)를 갖는다. **현장에서 켜기 전에 실기 확인이 필수다**(§10) |
| 개루프 이탈 | 호를 짧게(기본 470 mm) 잡고 매번 goto 가 보정한다 |
| 통로 폭 대비 반경 | 운영자가 `corner_arc_radius_mm` 로 정한다. 기본 300 mm |
| 호 완료 판정 실패 | 타임아웃 후 `UmStop` + goto 폴백(§6) |
| `cp_probe` 처럼 라우트 이름이 `routes.temp.json` 에 남음 | 무해하다. `UmSchedulerThis` 는 영속 `routes.json` 을 건드리지 않는다 |

## 9. 범위 밖

- **코너 깎기(사전 호)** — §3.1. FMS 점유 관제 위반이다.
- **`UmDrive` 기반 빠른 회전** — §3.2. `operatingMode` 를 깬다.
- **`UmSetConfig` 로 플래너 튜닝** — §3.3. 막혀 있다. 열리면 이 설계보다 싸므로 별도 추적.
- **`cmd:"follow"`(경로 추종)** — 존재는 확인했으나 코너 거동 미검증.
- **속도 상향** — 별개 과제.

## 10. 현장 적용 전 필수 확인

**곡선 주행 중 장애물 거동.** 빈 공간에 장애물을 놓고 `curve` 로 접근시켜
서는지 / 감속하는지 / 그대로 가는지 관측한다. `scripts/probe-jibot-route-goto.py` 가
`UmSchedulerThis` 발신과 pose/mode 샘플링을 이미 갖고 있으므로 `curve` 스텝만 얹으면 된다.

**그대로 간다면 이 기능을 현장에 켜서는 안 된다.** 그 경우 §3.3(`UmSetConfig` 벤더 확인)로
돌아가거나, 호를 장애물이 없다고 보장된 구간에만 적용하는 별도 설계가 필요하다.

성공 판정은 before/after 로 잰다 — 코너 통과 시간, `vel_r` 이 0인 구간의 길이.
어댑터 로그의 `[ORDER NODE REACHED]` / `[ORDER STEP CLEAR]` 타임스탬프로 계산할 수 있다.
