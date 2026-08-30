# goto-arrival-theta

### 목표
- goto 도착마다 heading(theta)이 바뀌는 원인 규명 및 수정
- 채택안: order `nodePosition.theta` 우선(rad→deg), 없으면 target 까지의 bearing
  (= 가는 방향) 을 `poseTh` 로. poseTh 생략은 실기에서 폐기됨

### 지금
- 재수정 + 전체 회귀 완료. 62 재배포 대기

### 실기 결과 (2026-08-22 22:46~22:52, HN-SH6-TR-002 / 192.168.101.62)
- 보낸 명령: `{"#CMD#":"UmGoto","target":"pose","poseX":16350.0,"poseY":91.0}` (poseTh 없음)
- 로봇: status=Stopped, pos 고정(16301,-1155). 3회 재전송 모두 **위치 변화 0**
- 로봇 error 프레임 없음 → urobot 이 **조용히 무시**. UmGoto 는 ret:none 이라 ack 도 없음
- 같은 12시간 안에서 poseTh 있는 UmGoto 48건은 정상 주행 → **poseTh 는 필수**
- `th` 원시값 180 / 90 → `_vehicle._th` 단위는 **도(°)**, poseTh 와 동일

### 완료
- 원인: `_send_node_goto`가 PathPoint pose를 찾으면 `goto_xyz(x,y,map_theta)`
  → `UmGoto{target:"pose",poseX,poseY,poseTh}` 로 heading 강제.
  hana.json/hana2.json PathPoint 44개 전부 theta=0.00 → 매 도착마다 0°로 회전
- `adapter_jibot.py` `_node_goto_theta_deg()` 신설: nodePosition.theta(rad)→deg,
  없으면 None. 맵 PathPoint theta는 폴백으로 쓰지 않음
- `_send_node_goto` 가 map theta 대신 `_node_goto_theta_deg(node, (x,y), map_theta)` 사용
- `_handle_goto_nearest_node_instant_action` pathPoint 분기도 같은 bearing 사용
  (시뮬레이터 분기는 그대로)
- 계약 `AmrMotion.goto_xyz` z 는 다시 **필수**(None 의미 없음). jibot-client 는
  z=None 이면 ValueError — 조용히 안 움직이는 실패를 시끄럽게 바꾼다
- 페이로드: `{"target":"pose","poseX":..,"poseY":..,"poseTh":<bearing>}`
- 도착 판정은 x/y만(`_node_xy`) → heading 선택이 완료 판정에 영향 없음

### 다음
- 192.168.101.62 (및 .61) 재배포 후 실주행 확인
- 원하는 heading 은 FMS 가 노드의 nodePosition.theta(radian) 로 지정
- 한계: bearing 은 직선 방향. 곡선 접근이면 진입 방향과 미세하게 다를 수 있음
  (이 현장은 인접 PathPoint 1구간씩이라 직선)

### 검증
- 신규 3건 RED 확인 후 GREEN
  (`test_path_point_goto_heads_the_travel_direction_when_the_order_has_none`,
   `test_path_point_goto_keeps_its_heading_when_already_on_the_node`,
   `test_path_point_goto_falls_back_to_the_map_heading_without_telemetry`)
- 기존 3건 기대값 갱신(`test_send_node_motion_uses_pose_for_path_point`,
  `test_path_point_without_node_position_waits_for_pose_arrival`,
  `test_path_point_mode_selects_path_point_and_uses_pose_motion`)
- `scripts/run-tests.sh` 전체: **13 failed, 2192 passed**
- 실패 13건(airshower/pio/recipes/unknown-config-key)은 HEAD detached worktree 에서도
  동일하게 13건 실패 → 이번 변경과 무관한 기존 실패
- 실패했던 p36 케이스 재계산: pos(16301,-1155) → target(16350,91),
  bearing = atan2(1246,49) ≈ 87.7° → poseTh 실림
