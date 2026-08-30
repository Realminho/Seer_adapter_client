# stopcharging-order-action-unsupported

### 목표
- `Order action failed: Unsupported order action type: stopCharging` 원인 규명
  (1_01CH 충전 노드에서 출발할 때 발생)

### 지금
- 원인 확정, 재현 완료. 수정 방향 결정 대기.

### 완료
- 원인: `_dispatch_order_action()` adapter_jibot.py:4200 의 분기 중
  stopCharging에 해당하는 것이 없음. switchMap / startCharging(전용 분기 :4220) /
  is_hardware_action / _action_registry.has / _is_jibot_command_instant_action
  전부 미해당 → :4234 fallthrough
- startCharging도 JIBOT 커맨드가 없어서 :4220에 전용 분기를 받았으나
  stopCharging은 받지 못함 (비대칭)
- 구현 자체는 존재하나 instant action 경로 전용:
  `instant_actions_accept_procedure` :5554 → `_handle_stop_charging_instant_action`
  :6828 → `_run_stop_charging` :6869 (UmStop + relay 해제 + charging=false 검증)
- 설계 맥락: core/registry.py:215-219 — stopCharging은 factsheet agvActions에서
  의도적으로 제외됨("WebUi만 보낼 수 있다"). FMS는 광고되지 않은 action을
  order에 실어 보내고 있음
- 참고: dock work 노드 도착 시에는 이미 자동으로 충전을 끊음
  (`_process_v3_node_step` :5364, `config.dock.stop_charging_on_arrival` 기본 True)

### 다음
- 수정 방향 결정: (a) `_dispatch_order_action`에 stopCharging 분기 추가 +
  factsheet 광고, (b) FMS가 order에 싣지 않도록 조정

### 검증
- 재현: `_dispatch_order_action`에 owner_id="1_01CH"로 직접 투입
  - startCharging → FINISHED: "UmDock sent; charging follows robot status"
  - stopCharging  → FAILED:   "Unsupported order action type: stopCharging"
- 분기 술어 직접 확인: is_hardware_action=False, registry.has=False,
  is_jibot_command=False (startCharging도 registry.has=False — 전용 분기로 처리됨)
