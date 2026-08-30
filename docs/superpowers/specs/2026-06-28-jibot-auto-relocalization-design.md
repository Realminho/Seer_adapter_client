# JIBOT Auto Relocalization Guard Design

작성일: 2026-06-28
상태: 설계 승인됨, 구현 계획 대기
범위: `unified-amr-adaptor` JIBOT adapter only

## 문제 / 목표

JIBOT가 `localization/path lost; relocalization required` 계열 상태를 보고하면
운영자가 수동으로 위치를 다시 잡아야 한다. 현장 확인 결과 `/jrobot_status`는
`system_error_code: 0`이고 estop/motor fault도 없을 수 있으므로, lost 신호가 항상
RobotStatus fault로 남는 것은 아니다. 어댑터는 JIBOT TCP 상태(`UmGetLocState.score`,
`UmGetRobotInfo.mode/status/station`)와 VDA5050 진행 상태를 함께 보고, 안전한 조건에서만
자동 `UmLocalize`를 1회 시도해야 한다.

목표는 "어디서든 자동으로 위치를 추정"하는 것이 아니라, **마지막으로 확실히 알던
station/lastNodeId 위치에 로봇이 정지해 있다고 판단되는 경우만 자동 relocalize**하는
것이다. 기준 위치가 불확실하면 자동 실행하지 않고 운영자 조치가 필요하다는 상태만
노출한다.

## 배경 / 현재 부품

- JIBOT relocalization 명령은 `UmLocalize(target, goal, poseX, poseY, poseTh)`이고,
  어댑터의 VDA5050 `initPosition` instant action이 이 명령으로 매핑된다.
- `UmLocalize`는 JIBOT API상 `ret:none` fire-and-forget 명령이다. 따라서 "명령 전송 성공"을
  복구 성공으로 취급하면 안 되고, 이후 `UmGetLocState.score`, `UmGetRobotInfo.status`,
  `station`, pose 변화로 검증해야 한다.
- 지도 노드 좌표는 `UmGetMap` 기반 `_map_nodes` 캐시와 runtime map snapshot에 있다.
- `lastNodeId`/station 관련 보수적 정책은 이미 별도 작업에서 다뤘다:
  `2026-06-24-last-node-capture-mode-and-station-bootstrap-guard-design.md`.
- 에러 관측성 작업은 `/jrobot_status.system_error_code`를 `JIBOT_SAFETY` 정보로 노출하는
  방향이다. 이 기능은 그 값을 guard 조건으로 읽되, 에러 심각도 정책은 새로 만들지 않는다.

## 결정

| # | 결정 | 이유 |
|---|------|------|
| 1 | 기본값은 `enabled = false` | 로봇 위치를 강제로 보정하는 기능이라 현장별 승인 필요 |
| 2 | 자동 기준은 최근 정상 `station`/`lastNodeId`만 사용 | 마지막 저장 pose보다 오보정 위험이 낮음 |
| 3 | 활성 order 중에는 자동 relocalize 금지 | order 진행 중 pose 강제 보정은 경로/진행 상태를 깨뜨릴 수 있음 |
| 4 | 로봇 정지 상태에서만 시도 | 주행 중 lost는 먼저 정지/취소가 필요 |
| 5 | `system_error_code == 0`일 때만 시도 | 센서/맵/모터 fault가 있으면 relocalize보다 fault 해소가 우선 |
| 6 | 1회 시도 후 cooldown 적용 | 반복 `UmLocalize`로 상태를 더 악화시키지 않음 |
| 7 | 성공은 후속 상태로 검증 | `UmLocalize`가 성공 응답을 주지 않기 때문 |
| 8 | 기준 위치가 없으면 operator-required 상태만 노출 | "모르는 위치"를 자동으로 안다고 가정하지 않음 |

## Config

신규 섹션:

```toml
[jibot_relocalization]
enabled = false
max_attempts = 1
min_lost_duration_sec = 3.0
cooldown_sec = 60.0
min_recovered_score = 1.0
require_station = true
```

의미:

- `enabled`: 자동 relocalization 전체 gate.
- `max_attempts`: lost episode 하나에서 허용하는 자동 시도 횟수. 1차 구현은 1만 지원해도 된다.
- `min_lost_duration_sec`: transient lost 문구/score 흔들림을 거르기 위한 지속 시간.
- `cooldown_sec`: 실패 또는 시도 후 재시도 억제 시간.
- `min_recovered_score`: 복구 판정용 최소 localization score. JIBOT score 범위가 아직 명확하지
  않으므로 기본값은 낮게 두고, 운영 로그로 조정한다.
- `require_station`: true면 기준 node가 station/lastNodeId로 확정되어야만 자동 실행.

## Lost 감지

다음 신호가 `min_lost_duration_sec` 이상 지속되면 "lost episode"로 본다.

- JIBOT status/mode 문자열에 `lost`, `localization`, `relocalization`, `path lost` 계열 문구가
  포함된다.
- `UmGetLocState.score`는 1차 구현에서 lost 진입 조건으로 쓰지 않는다. JIBOT score 범위와
  정상/비정상 경계가 아직 운영값으로 확정되지 않았기 때문이다. score는 복구 검증에만 보조로
  사용한다.
- 향후 `/lost_info` 또는 `/jfault` 소비가 추가되면 같은 detector에 입력만 추가한다.

단, `system_error_code != 0`, estop, motor_error, pc_enable false, motor_enable false이면
auto relocalize 후보가 아니라 "operator required"로 둔다.

## 기준 위치 선택

우선순위:

1. 현재 또는 최근 정상 `station`이 있고 `_map_nodes[station]` 좌표가 있으면 사용.
2. `lastNodeId`가 있고 `_map_nodes[lastNodeId]` 좌표가 있으면 사용.
3. 둘 다 없으면 자동 실행하지 않는다.

마지막 저장 pose(`runtime/jibot-position.json`)는 1차 자동 기준으로 사용하지 않는다. 로봇이
수동으로 밀렸거나 지도 밖에 있을 때 오보정 위험이 크기 때문이다. 향후 현장 요구가 있으면
별도 config로 opt-in 한다.

## 실행 흐름

`JibotAdapter`에 작은 상태 머신을 둔다.

```
IDLE
  lost detected and guards pass
  -> CANDIDATE

CANDIDATE
  choose station/lastNodeId map pose
  if no reference: publish operator-required, back to IDLE/cooldown
  else send UmLocalize(target="pose", goal=node_id, poseX/Y/Th=node pose)
  -> VERIFYING

VERIFYING
  poll existing status stream for a bounded window
  recovered if lost text clears and localization score >= min_recovered_score
  success -> mark recovered, cooldown, IDLE
  failure -> publish operator-required, cooldown, IDLE
```

실행은 기존 adapter loop 안에서 한다. 별도 thread/process를 만들지 않는다. 명령 전송은
이미 있는 vehicle method `um_localize`를 재사용한다.

## 상태 노출

VDA5050 `information[]`에 별도 `JIBOT_RELOCALIZATION` infoType을 추가한다.

항상 내보낼 reference:

- `enabled`
- `state`: `IDLE`, `CANDIDATE`, `VERIFYING`, `RECOVERED`, `OPERATOR_REQUIRED`, `COOLDOWN`
- `reason`: lost 감지 또는 guard 실패 이유
- `referenceNodeId`
- `attempts`
- `lastAttemptTime`
- `lastResult`

성공/실패를 VDA5050 `errors[]`로 승격하지 않는다. 이 기능은 자동 복구와 관측성만 담당한다.
운영 정책상 치명 오류로 취급할지는 별도 작업이다.

## 실패 / 엣지

- 기준 node가 map에 없음: 자동 실행 안 함, `OPERATOR_REQUIRED`.
- active order 있음: 자동 실행 안 함, reason=`order_active`.
- motor/estop/fault 있음: 자동 실행 안 함, reason=`robot_fault`.
- `UmLocalize` 전송 예외: FAILED로 기록, cooldown.
- 검증 timeout: `OPERATOR_REQUIRED`, cooldown.
- relocalize 후 기준 node 확인이 되지 않음: 실패로 취급한다. station 기준이면 station 일치,
  lastNodeId 기준이면 pose가 해당 node 근처인지 확인한다. score만으로 성공 처리하지 않는다.

## 테스트

- detector: lost 문자열이 `min_lost_duration_sec` 이상 지속될 때만 candidate가 되는지.
- guard: active order, estop, `system_error_code != 0`, motor disabled에서 자동 실행하지 않는지.
- reference selection: station 우선, lastNodeId fallback, map 좌표 없으면 skip.
- command: 조건 충족 시 `um_localize(target="pose", goal=node_id, poseX/Y/Th=...)`가 1회만 호출되는지.
- verification: lost 해제 + score 회복 + station 일치 시 recovered, timeout 시 operator-required.
- cooldown/max attempts: 같은 episode에서 반복 호출하지 않는지.
- information publish: stale 값이 남지 않도록 고정 key set을 빈 문자열로라도 계속 송출하는지.

## 비범위

- 로봇이 실제로 어디 있는지 비전/라이다로 전역 재추정하는 기능.
- 마지막 저장 pose 기반 자동 relocalize.
- `/jrobot_status.system_error_code`를 VDA5050 `errors[]`로 승격하는 정책.
- JIBOT map/routes 자동 재배포 또는 JManager map reload.
