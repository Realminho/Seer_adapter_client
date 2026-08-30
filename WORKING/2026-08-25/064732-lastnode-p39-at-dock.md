# lastnode-p39-at-dock

### 목표
- HN-SH6-TR-002(10.8.8.8) 가 1_01CH 에 있는데 노드가 p39 로 남는 원인 규명
- adaptor restart 로 안 바뀌는 이유

### 완료 — 현장 로그로 확정 (2026-08-25 06:58 실측)
- **지금 어댑터는 p39 를 보고하지 않는다.** `/run/amr-adaptor/HN-SH6-TR-002/state.json`
  → `lastNodeId=""`, `operatingMode=STARTUP`, `agvPosition=None`,
    `errors=[LAST_NODE_ID_MISSING, VEHICLE_LINK_LOST]`
- 원인: **06:46:08 리부팅 후 JIBOT TCP 10.8.8.8:7273 이 안 열림**
  - `urobot.service` active, `roslaunch urobot.launch` 떠 있음
  - 그러나 `tf2odomNode: lookupTransform('odom','map') failed: "map" ... does not exist` 무한 반복
    → 맵 미로드 = 로컬라이제이션 미기동 → umcl TCP 서버 미개방
  - 어댑터는 06:47:02 부터 5초마다 `[JIBOT RECONNECT FAILED] Errno 111` 만 반복
- pose 가 0 건이면 `_update_nearest_node_from_position` 자체가 호출되지 않음
  → nearest/lastNodeId 를 갱신할 경로가 없음 → **어댑터 재시작은 아무 효과 없음**
- 리부팅 전에는 정상 동작이었음:
  - `[LAST NODE FALLBACK] nearestNodeId=1_01CH gap≈146mm` — 06:20:02 / 06:27:48 /
    06:43:20 / 06:45:12 / 06:45:46 재시작마다 매번 1_01CH 로 잡음
  - 06:30:06 `[ORDER NODE DOCK] id=1_01CH command=UmDock` → 06:31:16 STEP CLEAR,
    ORDER COMPLETE. 도킹 오더 20260825-8-1 정상 종료
  - pos=(10661,-2547), 1_01CH 까지 146mm
- 배포 config 확인: `last_node_capture_mode = "proximity"`, `nearest_node_mode = "pathPoint"`,
  `idle_last_node_reach_xy = 200`, `use_nearest_node_as_last_node_when_missing = true`
  - 이 로봇 맵(lab2m)은 `nodes=53 pathPoints=53` 이고 **1_01CH 가 PathPoint 후보에 들어 있음**
    (로그의 `nearestNodeId=1_01CH` 가 증거). 다른 현장 맵(hana.json)은 1_01CH 가 Dock 전용이라
    pathPoint 모드에서 후보 제외 — 이 건에는 해당 없음
- 어댑터 WebUI 는 `state.last_node_id` 를 그대로 찍으므로(web/render.py:641) 지금은 "—" 로 보임
  → **사용자가 보는 p39 는 FMS(12.230.57.248:11883) 쪽에 남은 마지막 값**

### 다음 (해결됨)
1. urobot 맵 로드/재로컬라이즈 → `ss -ltn | grep 7273` 로 개방 확인
2. 열리면 어댑터가 곧바로 `[LAST NODE FALLBACK] nearestNodeId=1_01CH` 로 복구되는지 확인
3. 그래도 FMS 화면이 p39 면 FMS 측 캐시/판정 문제 — 어댑터 밖

### 검증
- `python3 -c 'import json;print(json.load(open("/run/amr-adaptor/HN-SH6-TR-002/state.json"))["lastNodeId"])'`
- `journalctl -u urobot --since "-5min" | grep -c "map\" passed to lookupTransform"`

---

## 후속 (07:03) — JIBOT 복구 후 재조사, 진짜 원인 확인

### 현재 상태 (정상)
- 07:01:26 `[JIBOT MAP UPDATED] nodes=53 pathPoints=53`, 07:01:28
  `[LAST NODE FALLBACK] nearestNodeId=1_01CH gap=145.2` → 7273 개방됨
- state.json(07:02:49): `lastNodeId="1_01CH"`, `operatingMode=AUTOMATIC`, `orderId=""`
  → **어댑터는 p39 를 보고하지 않음**

### FMS 가 p39 로 굳은 원인 — 06:58:59 좀비 오더
- 06:58:59.229 FMS → `orderId=FMSMR-b06f80999140-R0-S0-n9zp nodes=1` (노드 = **p39**)
- 이때는 JIBOT 링크가 없던 시각(opMode=STARTUP). 그런데 어댑터가 **수락**하고
  `[ORDER NODE GOTO_POINT] seq=0 id=p39` 시도
- 06:58:59.242 `[ORDER QUEUE] worker error: 'NoneType' object has no attribute 'write'`
  → JIBOT writer 가 None. 워커가 죽고 오더는 active 인 채 좀비로 남음
- 06:58:59.402 후속 오더 `...-S1-rude nodes=4` → `[ORDER REJECTED] ... reason=current order in progress`
- 06:59:35 `cancelOrder` → `stop_motion failed: 'NoneType' ...`, 큐는 비움(orderId="")

### 남은 부작용 (지금도 발행 중)
- `state.errors` 에 `ORDER_CURRENT_NOT_FINISHED` WARNING 이 timestamp 06:58:59 그대로 잔존
  ("Current order is still in progress"), 그런데 `orderId=""`
- 에러 정리 경로는 `_accept_v3_order_for_queue` 의 `self.state.errors = []`(3762) 와
  `clearErrors` instantAction(7405) 둘뿐 → **cancelOrder 로는 안 지워짐**
- FMS 는 이 warning 을 계속 받으니 "p39 오더 진행 중" 으로 유지 → 화면 p39 고착

### 버그 후보 2건
1. **JIBOT 미연결 상태에서 오더 수락** — `order_accept_procedure` 에 링크 체크 없음.
   `_vehicle` writer 가 None 이면 첫 send 에서 AttributeError → 워커 사망 + 좀비 오더.
   → 연결 없으면 `ORDER_REJECT`(또는 링크 복구까지 대기)로 막아야 함
2. **cancelOrder 가 order 관련 error 를 정리하지 않음** — orderId 를 비우면서
   ORDER_CURRENT_NOT_FINISHED 는 남겨 둠. FMS 에는 계속 미완료로 보임

### 즉시 조치
- FMS 에서 오더 재발행 → `_accept_v3_order_for_queue` 가 errors 를 비우고 정상 처리됨
- 또는 `clearErrors` instantAction, 또는 어댑터 재시작(현재는 JIBOT 정상이라 안전)

---

## 수정 (2026-08-25)

### 완료
- `adapter_jibot.py` 모듈 상수 `_ORDER_SCOPED_ERROR_TYPES` 추가
  (ORDER_CURRENT_NOT_FINISHED / ORDER_UPDATE_ID_INVALID / ORDER_START_NODE_INVALID /
   ORDER_START_SEQUENCE_ID_INVALID). ORDER_JSON_PAYLOAD_INVALID·ACTION_* 는 제외 —
  페이로드 파싱 실패는 오더 수명에 묶인 상태가 아니고, 지우면 FMS 결함을 감춘다
- **가드 1** `_handle_v3_order`: `_is_jibot_connected()` False 면 오더 거절.
  형식 검증(nodes=0) 뒤에 배치 — 잘못된 페이로드는 원래 사유로 거절돼야 함.
  errorType 은 형제 가드와 같은 `UNKNOWN_ERROR`. `JIBOT_CONNECTION_LOST` 를 쓰면
  `_refresh_jibot_connection_errors`(2696)가 매 주기 그 타입을 통째로 걷어내고
  자기 것만 다시 넣어 거절 사유가 지워진다. 링크 끊김 자체는 그 FATAL 오류가 계속 보고함
- **가드 2** `_drop_order_after_worker_crash` 신설 + 워커 `_on_done` 에서 호출.
  수락 시점 검사만으로는 주행 중 링크 끊김을 못 막는다 — "워커가 죽으면 오더는
  반드시 끝난다" 를 여기서 지킴. `_clear_order_queue` → `order_reject(FATAL)` →
  `_clear_cancelled_order_state` → `request_state_publish` 순서
  (order_reject 가 errorReferences 키로 state.order_id 를 쓰므로 비우기 전에 호출)
- **가드 3** `_clear_cancelled_order_state`: `_ORDER_SCOPED_ERROR_TYPES` 를 errors 에서 제거.
  cancel 경로는 `_update_instant_action_status` → `request_state_publish` 로 이미 발행됨
- 테스트 신규 `adaptor/tests/test_order_zombie_guards.py` 4건 전부 통과
  - 링크 다운 시 오더 거절 + order_id 안 남음
  - 거절된 오더가 다음 오더를 막지 않음 (사고의 핵심 증상)
  - 워커 크래시 시 오더 폐기 (주행 중 링크 끊김 경로)
  - cancelOrder 가 ORDER_CURRENT_NOT_FINISHED 를 정리

### 검증
- `tests/test_adapter_jibot_v3_order.py` 726건 통과
- `test_charge_in_place / test_set_map_instant_action / test_initial_pose_and_map /
   test_adapter_dispatch / test_action_state_publication / test_dock_charge_handover` 71건 통과
- 전체 스위트 25 fail 중 **14건은 HEAD(37e2327) 에서도 실패하는 기존 실패**
  (test_recipes_config 5, test_unknown_config_key 4, test_airshower_* 5) —
  detached worktree 로 대조 확인함. 나머지 11건(v3_order 10 + test_senders 1)은
  다른 세션이 같은 파일을 편집 중이라 생긴 일시 실패, 재실행 시 전부 통과

### 다음 (해결됨 아님 — 아래 재시작 지점으로 이동)
- **배포 미확인**: 10.8.8.8 ssh 가 07:1x 이후 timeout. `/home/ucore/adaptor` 가
  git 체크아웃인지, 어느 커밋인지 확인 못 함. 이 수정은 로컬 워킹트리에만 있음
- 배포 전까지 같은 사고(리부팅 직후 오더 → 좀비)가 그대로 재발함

### 추가 확인
- 크래시 처리가 남기는 FATAL 이 다음 오더를 막지 않음을 확인.
  `_derive_operating_mode`(1219)는 errors 를 보지 않고, `_handle_v3_order` 에도
  FATAL 게이트가 없다. FATAL 은 1442 의 detail 문자열("FAULT")에만 쓰인다.
  테스트에 후속 오더 수락 검증을 추가함
- 링크 가드가 `_update_v3_order_for_queue` 앞에 있어, 링크가 잠깐 끊긴 순간에는
  **이미 active 인 오더의 orderUpdateId 갱신도 거절**된다. 예전엔 처리됐다.
  다음 명령에서 어차피 워커가 죽었고 이제는 가드 2가 받아내므로 의도한 동작
- 커밋 주의: `adaptor/adapter_jibot.py` 에 다른 세션의 dock handover 245줄이 섞여 있다.
  파일 통째 커밋 금지 — 위 4개 hunk + `tests/test_order_zombie_guards.py` 만 선택 스테이징할 것

---

### 다음 (재시작 지점)
1. `adaptor/adapter_jibot.py` 에서 **내 hunk 4개만 선택 스테이징**해 커밋
   - 모듈 상수 `_ORDER_SCOPED_ERROR_TYPES` (@@ -89 부근)
   - `_handle_v3_order` 링크 가드 (@@ -3725 부근)
   - `_on_done` → `_drop_order_after_worker_crash` 호출 (@@ -4217 부근)
   - `_clear_cancelled_order_state` errors 필터 + `_drop_order_after_worker_crash` 신설 (@@ -8460 부근)
   - `adaptor/tests/test_order_zombie_guards.py` 신규 추가
   - **금지**: 파일 통째 `git add`. 같은 파일에 다른 세션의 dock handover 245줄
     (@@ -5900,3 +5944,245 @@) 이 섞여 있고 그쪽 config.py/config.toml 과 짝이다
2. 10.8.8.8 ssh 복구되면 `/home/ucore/adaptor` 가 git 체크아웃인지·어느 커밋인지 확인 후 배포
   - 확인 명령: `cd /home/ucore/adaptor && git log --oneline -2 && git status --porcelain`
3. 배포 후 회귀 확인: 리부팅 직후(7273 닫힘) 오더를 넣어
   `[ORDER REJECTED] ... reason=vehicle link down` 이 뜨고 다음 오더가 정상 수락되는지
