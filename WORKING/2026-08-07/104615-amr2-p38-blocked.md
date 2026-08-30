# amr2-p38-blocked

### 목표
- AMR #2(192.168.101.62, HN-SH6-TR-002)가 p38까지 이동하지 못한 원인 규명

### 지금
- 근본 원인 확정. 이어서 `mode="move"` -> goto 폴백 설계 승인받고 스펙 커밋(609382c).
- 사용자 결정: distance는 짧게 유지하는 게 맞고, move 후 모자라면 goto로 마저 이동.
  완료 판정은 "출발 확인 + 이동거리" 복합. 노드 도착 판정은 zone 유지.
- 보류(1번 끝나면 재논의): (2) 폴백 goto마저 실패 시 처리, (3) 룰별 옵트인 여부.

### 완료 (근본 원인, 2026-08-07 실측)
- order `20260807-2-1` 경로: `1_01CH -> p39 -> p40 -> p37 -> p38`.
- **첫 구간(1_01CH -> p39)에서 멈춰 p40/p37/p38은 시도조차 안 됨.**
- 로봇 `config/config.toml`의 motion_rule:
  `[[motion_rules]] to="p39" from="1_01CH" mode="move" distance=-2000 speed=150`
  → UmGoto가 아니라 **상대 이동(UmSchedulerThis manual_move) -2000mm**로 주행.
- 실측: 출발 `x=10650` → 종료 `x=12681` (약 2031mm 이동, 명령값대로 정상 완주).
  p39 목표는 `(13446, -2505)`. **765mm 부족** → square zone radius 200 밖.
- 10:44:22.397 `[ORDER NODE UNREACHED] id=p39 pos=(12681.0,-2473.0) target=(13446.0,-2505.0) radius=200.0`
- 장애물/브레이크 정지 아님: 명령 2000mm를 다 이동한 뒤 Stopped. 배터리 67%, motor=True, loc_score 771.
- 취소 아님: 08-05 케이스와 달리 cancelOrder 없음.

### 부수 발견
1. `_wait_until_node_position_reached`(adaptor/adapter_jibot.py:4546)의 `while True`에
   **포기 경로가 없다.** UNREACHED를 1회 로깅/에러설정한 뒤 무한 폴링 →
   order가 실패하지도 진행하지도 않고 영구 정지. 10:44:22 이후 로그 완전 무음,
   로봇은 (12681,-2473)에 계속 주차 상태.
2. 같은 대기 루프의 UNREACHED 판정이 `wait_started_at` 기준 1초라 **로봇이 아직
   출발 전(Stopped)일 때 조기 발화**한다. 08-05 23:50:55, 08-06 15:09:51이 그 사례
   (move 송신 1초 뒤 제자리에서 UNREACHED). 오늘 건은 여기 해당 없음(16초간 실제 주행).
3. base node `1_01CH`(seq=0)의 `unclamp`/`clamp(BEFORE_LEAVE_NODE)` 액션이
   `[ORDER UPDATE SKIP] lastNodeSequenceId=0`으로 걸러져 **한 번도 실행되지 않았다.**
   p38 미도달의 원인은 아니지만 별도 확인 필요.

### 다음
- 구현 계획 `docs/superpowers/plans/2026-08-07-move-segment-goto-fallback.md` 실행.
  Task 1 설정키 → 2 `_send_node_goto` 추출 → 3 pause 가드 → 4 `_wait_until_move_settled`
  → 5 폴백 배선 → 6 pause 재개. 각 태스크 TDD + 커밋.
- 보류 항목 재논의: (2) 폴백 goto마저 실패 시 처리, (3) 룰별 옵트인 여부.
- (해결됨) 현장값 결정: distance는 짧게 유지. 아래는 초기 검토 기록.
- 결정 필요(현장값): `1_01CH -> p39` distance를 실제 구간거리에 맞출지
  (map상 1_01CH PathPoint 10515 → p39 13446 = **2931mm**, 오늘 출발점 기준 2796mm),
  아니면 이 룰을 지우고 일반 UmGoto로 되돌릴지.
- `mode="move"` 도착 판정 실패 시 order를 FAILED로 떨어뜨리는 탈출 경로 추가 검토.
- UNREACHED 지연을 "정지 시점 기준"으로 바꿔 조기 발화 제거 검토.

### 검증
- 이력 대조: `mode="move"`가 탄 p39는 **전부 UNREACHED**
  (08-05 23:29 dist=-1200 → 11888, 08-05 23:50/08-06 15:09 조기발화, 08-07 10:44 → 12681).
- 성공한 `[ORDER NODE REACHED] id=p39`(08-05 21:04, 08-06 14:02, 08-06 19:17)는
  전부 룰이 매칭 안 되는 방향(예: p38->p39)이라 `UmGoto pose(13446,-2505)`로 주행 → 13438~13603 도착.
- 로봇 runtime map `/home/ucore/adaptor/runtime/jibot-map.json`:
  1_01CH(PathPoint) 10515,-2538 / p39 13446,-2505 / p40 13361,-1077 / p37 16376,-897 / p38 16399,-1897.
- 로봇 설정: `reach_zone_shape="square"`, `reach_zone_scale=20`, `default_node_deviation_xy=10` → radius 200.
