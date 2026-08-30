# dock-fail-obstacle-brake

### 목표
- 2026-08-19 11:02 [ORDER NODE DOCK FAILED] (not charging within 200.0s) 근본 원인 해결

### 지금
- 구현/검증 완료. 배포 여부만 사용자 판단 대기

### 완료
- 실측: 11:02:13~11:05:33 JIBOT status 분포
  nrunto 1_01CH#brake 52 / nrunto 1_01CH 16 / #slowdown 8 / #watch 6
  -> 200초 대부분을 장애물 대기로 보냄. 충전기/접점 고장이 아님
- 원인: _wait_until_dock_charge_or_timeout이 monotonic deadline만 보고
  _is_jibot_obstacle_wait()를 무시. _wait_until_node_position_reached /
  _wait_until_move_settled는 둘 다 obstacle을 판정에서 제외 -> dock만 규약이 달랐음
- TDD 2사이클:
  (1) 장애물 대기 중에는 dock 타임아웃이 흐르지 않는다
  (2) 연속 장애물 대기가 한도를 넘으면 그래도 실패한다(DOCKING 고착 방지)
- 1사이클 구현이 2사이클 테스트와 모순 -> 연속 brake 한도를 fail_timeout_sec 재사용에서
  별도 knob으로 분리 (재사용하면 brake 제외가 무의미해짐)
- 추가: dock.obstacle_timeout_sec (기본 300.0, <=0 무제한)
  config.py / config.toml / adaptor/config/config.toml 3곳 반영
- 관측성: [ORDER NODE DOCK BLOCKED] / CLEARED / BLOCKED OUT 로그 추가
  (기존에는 200초 동안 어댑터 로그가 전무했음)

### 다음
- 전체 테스트 결과 확인
- 로봇 배포 여부는 사용자 판단 (.61 배포본은 더 오래된 빌드라 파일 단위 배포 부적합)

### 검증
- scripts/run-tests.sh tests/test_adapter_jibot_v3_order.py -k dock -> 58 passed
- scripts/run-tests.sh (전체) -> 2041 passed, 1 failed
  실패 1건은 tests/test_goto_nearest_node.py::test_timeout_stops_robot_and_fails
  (본 변경 이전부터 실패, 08-18에 변경 되돌려 재현 확인함. gotoNearestNode 경로로 무관)
