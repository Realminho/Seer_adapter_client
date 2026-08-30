# faster-shortcut-driving

### 목표
- 노드 단위 stop-and-go 를 없애고 연속 주행으로 만들기
- 주행 속도 상향 + "경로 정확 추종"보다 "최단 거리" 우선

### 지금
- 브레인스토밍(architectural). 사용자 확정: **A(+B) 본체, C 분리, 전부 옵션 게이트**.
  섹션별 설계 제시 -> 승인 대기 (승인 후 spec 문서 -> writing-plans)

### 완료
- 조사: stop-and-go 의 출처는 로봇이 아니라 어댑터.
  `_process_v3_node_step`(adapter_jibot.py:5919) 이 노드마다
  UmGoto -> `_wait_until_node_position_reached` -> `_settle_goto_arrival`
  (완전 정지까지 대기, :4465) 를 직렬로 돈다.
- 로봇은 이미 자유공간 플래너: config `plan` 섹션
  (global_plan_resolution=100, local_replan=true, local_plan_smooth=true,
   plan_diagonal_distance_factor=1.2) -> UmGoto target=pose 는 격자 최단경로.
- 속도 캡: `task-goto.max_vel=350`(mm/s) 인데 `robot.speed_trans_max=500`,
  `nav.limit_run_linear_max_vel=1000`. 여유 있음.
  가감속 `robot.speed_trans_acc/dec=1000`.
  출처: WORKING/2026-08-18/umgetconfig-192.168.101.61.json
- UmGoto 에는 speed 인자 없음(target/goal/poseX/poseY/poseTh/strict).
  속도 조절 수단은 UmSetConfig(+UmReloadConfig) 뿐.
  UmSetConfig 영속성/objs 형태는 미확인 (2026-08-18 로그의 미확인 위험 그대로).
- Edge.max_speed 는 v3 모델에 존재(protocol/vda5050_3_0/messages.py:273), 어댑터 미사용.

- 사용자 확정: FMS 는 **점유 관제한다** -> 지름길은 엣지 코리도어 안에서만.
- order 픽스처 실측 (jibot/site/2605-b205-demo/TestingOrderJson/, 5건):
  - base 릴리스는 **여러 개**. merger1_2 는 8노드 전부 released,
    merger2_1 은 8중 7 released(seq=8 하나만 관제로 막힘) -> 노드 병합 여지 충분.
  - 노드 간격 1116~4197mm (평균 ~2.5m). 8노드 = 완전정지 7회.
  - 도착 존 = allowedDeviationXY(2.0) x reach_zone_scale(20, config.toml:90) = **+-40mm** square.
  - edges 의 maxSpeed/length/rotationAllowed **전부 null** -> FMS 는 속도 지시를 안 준다.
  - 엣지 방향이 진행 방향과 반대인 케이스 존재 (e7 = F1_50_AS->F1_60 인데 진행은 F1_60->F1_50_AS)
    -> 엣지를 코리도어 기하로 쓰려면 방향 정규화 필요.
  - 노드 action 은 `{"key":"pio","value":1}` 형태 -> 액션 있는 노드는 병합 금지(정지 필요).
  - allowedDeviationXY=2.0mm 는 코리도어 폭으로 못 쓴다 -> 폭은 어댑터 config 로 잡아야 함.
- **별건 의심(다른 세션 소관)**: 이 픽스처의 nodePosition.theta 가 90.0.
  라디안이면 90rad 인데 `_node_goto_theta_deg` 는 math.degrees() 를 건다 -> 5156도.
  이 사이트(2605-b205-demo/HM-CAR-TR-002)가 도(°)로 보내는 것이면 변환이 어긋난다. 확인 필요.

### 다음
- 설계 승인 -> docs/superpowers/specs/2026-08-22-node-coalescing-design.md -> writing-plans
- C(속도 상향)는 이번 범위 밖. UmSetConfig 영속성 스파이크 선행 필요 (2026-08-18 로그의 미확인 위험)
- 옵션 표면 조사 완료: `[settings]` 섹션(config.toml:77), Settings dataclass(config/config.py:195~)
  에 기본값 추가해야 unknown_config_key 검증 통과. 선례: `last_node_release_xy = 0.0`(기본 off).
  per-robot 적용은 기존 `robot { config = "robot-a.toml" }`(robots.hcl.example) 로 가능.
- 설정은 부팅 1회 로드 -> 옵션 토글에 재시작 필요 (201806 세션 소관)

### 검증
- 아직 없음 (설계 단계)
