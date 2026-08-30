# far-arrival-completed-diag

### 목표
- AMR(192.168.101.61)이 목표 지점에서 멀리 떨어진 채 정지하고 완료 처리된 원인 규명

### 지금
- 근본 원인 확정. 조치는 사용자 승인 대기

### 완료
- 배포 config 확인: last_node_capture_mode = "proximity",
  use_nearest_node_as_last_node_when_missing = true, missing_last_node_reach_xy 미설정(=게이트 없음)
- 코드 확인: _capture_idle_proximity가 거리/오더 게이트 없이 _set_last_node 호출,
  매 state publish 경로(adapter_jibot.py:743)에서 실행 -> lastNodeId가 "최근접 노드 미러"가 됨
- proximity 모드에서는 idle_last_node_reach_xy(=100)가 아예 참조되지 않음
- 로그 실증: [LAST NODE FALLBACK] nearestNodeId=1_01CH gap=1299.2 (1.3m 떨어진 노드를 lastNodeId로)
- 로그 실증: 08-18 21:08:23 p40 주행 중 외부 cancelOrder -> stop_motion,
  2초 뒤 FMS가 e_p40_p37(시작=p40) 새 오더 발행. p40 도착 기록([ORDER NODE REACHED]) 없음
- 로그 실증: 08-19 10:32:09 UmDock(1_01CH) 시작 -> 10:33:24 외부 cancelOrder로 중단(75s, timeout 200s)
- 최근 6시간 [ORDER NODE REACHED]/[ORDER NODE DOCKED]/[ORDER COMPLETE] 0건
- p39->p40 구간 1506mm이므로 proximity 전환점은 목표 753mm 전 (도착 판정 반경 200mm의 3.7배)

### 다음
- (승인 시) config.toml last_node_capture_mode = "settled" 로 변경 + missing_last_node_reach_xy 게이트 설정 후 재시작
- 코드 측 방어가 필요하면 proximity 모드에 active-order 가드 추가를 TDD로 진행

### 검증
- ssh ucore@192.168.101.61 journalctl -u amr-adaptor.service 실측

---

## 2차: settled 전환 후에도 재발 (gap=1029.9)

### 확인
- 192.168.101.61 도달 불가: ping 100% loss, ssh "No route to host" (실측 중단)
  - 기존 이슈와 동일 징후: WORKING/2026-08-17/145900-amr-61-wifi-driver-hang.md
- 코드 확인: lastNodeId seed 경로(use_nearest_node_as_last_node_when_missing,
  adapter_jibot.py:8253)는 last_node_capture_mode와 **무관하게** 먼저 실행됨
- missing_last_node_reach_xy 기본값 0.0 = 게이트 없음 (config.py:239)
  -> lastNodeId가 비어 있으면 거리 무관하게 최근접 노드로 seed
- lastNodeId가 비는 시점 = 어댑터 프로세스 시작 시 (cancel은 lastNodeId를 비우지 않음,
  _clear_cancelled_order_state는 order_id/nodeStates/edgeStates/actionStates만 비움)
- settled는 seed를 되돌리지 못함: gap>100mm이거나 오더 진행 중이면 캡처 안 함

### 가설(로그 미확보)
- config 변경 후 재시작 -> 빈 lastNodeId -> 첫 pose에서 1029.9mm 떨어진 노드로 seed
- 이후 settled가 교정하지 않아 FMS엔 계속 "해당 노드에 있음"으로 보임
- 에러도 안 뜸: seed 경로는 print만 하고 error를 올리지 않음

### 다음 (.61 복구 후 즉시)
- journalctl -u amr-adaptor.service | grep -E 'LAST NODE FALLBACK|ORDER NODE REACHED'
  -> "[LAST NODE FALLBACK] ... gap=1029.9" 있으면 seed 경로 확정
  -> 없으면 재시작 누락(여전히 proximity) 의심, systemctl show -p ActiveEnterTimestamp 대조
- 조치안: missing_last_node_reach_xy = 200.0 (도착 반경과 동일) 추가

### 2차 확정 (사용자 실측, 10.6.6.6 경유)
- ActiveEnterTimestamp = 2026-08-19 11:00:44 (settled 반영 재시작)
- 11:00:53 [LAST NODE FALLBACK] nearestNodeId=p39 sequenceId=0 gap=1475.0
- [LAST NODE FALLBACK SKIP] 없음 -> missing_last_node_reach_xy 미설정(=게이트 0) 확정
- 즉 settled 전환은 정상 반영됐고, seed 경로가 mode와 무관하게 1.475m 떨어진 p39를 lastNodeId로 심음
- settled는 gap>idle_last_node_reach_xy(100)라 교정 불가 -> lastNodeId가 p39에 고착

### 조치 시 주의
- LAST_NODE_ID_MISSING은 ErrorLevel.CRITICAL (adapter_jibot.py:2540)
- 게이트를 걸면 노드에서 먼 위치로 부팅 시 seed가 막히고 CRITICAL 에러가 뜬다
- 정상 부팅(충전기/스테이션 정차)은 200mm 이내라 영향 없음

### 적용 (2026-08-19)
- .61(경유 10.6.6.6) /home/ucore/adaptor/config/config.toml
  - 백업: config.toml.bak-20260819
  - missing_last_node_reach_xy = 200.0 추가 (line 102), tomllib 파싱 확인
  - use_nearest_node_as_last_node_when_missing 주석의 "거리와 무관하게" 문구 수정
- 저장소 config.toml에도 동일 키 + 사유 주석 추가 (adaptor/config/config.toml에는 이미 0.0으로 존재했음 = 드리프트가 원인)
- 서비스 재시작은 권한 차단으로 미실행 -> 사용자 실행 필요

### 다음
- sudo systemctl restart amr-adaptor.service 후
  journalctl -u amr-adaptor.service -b | grep 'LAST NODE FALLBACK'
  -> [LAST NODE FALLBACK SKIP] gap=... > reach=200.0 이면 게이트 정상 동작

### 검증 완료 (재시작 13:38:01)
- [LAST NODE FALLBACK SKIP] nearestNodeId=p38 gap=280.0 > reach=200.0  -> 게이트 정상 동작
- 재시작 이후 ORDER QUEUED 없음 -> FMS가 CRITICAL로 오더 보류 중 (합의된 절차대로)
- 이전 인스턴스(11:02) 기록: [ORDER NODE DOCK FAILED] not charging within 200.0s
  -> 충전 시팅 자체 실패. 이번 건과 별개 이슈로 남김

### 남은 관찰
- SKIP 로그가 state publish마다(약 5초) 반복 -> off-node 대기 시 하루 ~17k 줄. journal 용량 이슈와 겹침
- gap=280.0: 게이트 200보다 큼. 정상 정차 위치가 200mm를 자주 넘으면 부팅마다 수동 복구 필요
- gotoNearestNode 타임아웃 실패 시에도 lastNodeId를 세팅하는 기존 결함 존재
  (tests/test_goto_nearest_node.py::test_timeout_stops_robot_and_fails) -> 복구 경로에 같은 구멍
