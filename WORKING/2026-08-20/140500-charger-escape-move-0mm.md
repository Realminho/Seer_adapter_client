# charger-escape-move-0mm

### 목표
- 1_01CH→p39 충전베이 탈출 상대이동이 0.0mm로 실패하는 근본 원인 규명 + 수정

### 지금
- 소스 수정 + 테스트 + 로봇 배포 완료. 실주행 실증만 남음

### 완료
- 근본 원인 확정: 중복 UmStop이 직후의 move를 덮어씀
  - 실패(로봇시계 10:49:11 / 12:51:10): UmStop(2/2) 뒤 **1~2ms** 만에 UmSchedulerThis(manual_move) 발행
    → JIBOT은 mode=Stop 유지, pos 변화 0, 10초 뒤 travelled=0.0 판정
  - 성공(10:50:34): 직전 UmStop 없음(이미 Stop 안정) → 2.3초 뒤 ModeMove/moving, travelled=2018.3
  - 두 실패 모두 UmStop(1/2)만으로 이미 충전이 끝나 있었음(ok=True가 즉시 반환됨). 2/2는 불필요했음
- 수정 `adaptor/adapter_jibot.py`
  - `_stop_charging_after_dock_work`: 반복 전 `_is_vehicle_charging()` 재확인 → 이미 멈췄으면 추가 UmStop 생략
  - `_ensure_not_charging_before_order_motion`: 충전 정지 성공 후 settle 대기(기본 1.0s) 뒤 반환
- `stop_charging_motion_settle_sec` 노브 추가 (config.py / config.toml x2)
- 테스트: 기존 4건을 새 계약으로 갱신, 신규 4건 추가

### 다음
- 사용자가 order 재시작 → 1_01CH→p39 실주행으로 실증
- 커밋 보류: adapter_jibot.py·config.py·config.toml·test_adapter_jibot_v3_order.py 모두 다른 세션 미커밋 변경분과 섞여 있음. 분리 커밋 여부는 사용자 판단

### 검증
- 대상 7건 `.venv/bin/python -m unittest ...` → OK
- 로봇 배포본 diff 결과 adapter_jibot.py·config/config.py 모두 **내 변경분만** 차이남(다른 세션 dock-brake 작업은 이미 배포돼 있음) → 2파일 교체가 안전
- 전체 스위트 `tests.test_adapter_jibot_v3_order` → Ran 675 tests, OK (수정 전 669 + 신규 6)
- 인접 모듈 `test_goto_nearest_node test_charge_in_place test_charge_circuit test_mqtt test_config_adapter` → Ran 32, OK
- 로봇 배포 완료(로봇시계 15:01, 서버 14:58): adapter_jibot.py + config/config.py 교체(백업 .bak-20260820-1430), systemctl restart amr-adaptor, MainPID 362360
- 재기동 후 UmConnect 성공, MQTT CONNECTED(15:01:02.327) → subscribe(.373) 순서 확인 (downlink 정상 무장)
- 참고: 로봇 시계가 서버보다 약 2분 47초 빠름. 로그 대조 시 보정 필요

### 로그 점검 (서버 15:51 기준)
- MQTT 수정 실증됨: 15:06:47(서버) order dispatch → orderCompareStatus WAITING→**MATCH**→COMPLETED, 로봇 journal에 ORDER RECEIVED/QUEUED, p39→1_01CH 주행 완료
- adaptor 안정: MainPID 362360 유지, NRestarts=0, Traceback/CRITICAL 0건, 15:01 이후 MQTT 끊김 0건
- 0mm 수정은 **아직 미발화** — `already stopped — skip UmStop` 0건, `settle ... before motion` 0건. 충전기 탈출 order가 아직 안 걸림
- 내 변경과 무관한 발견 2건
  1) 로봇 15:17:56 `[CHARGE CIRCUIT] open-relay publish failed` — rostopic pub 타임아웃. 다른 세션의 로컬 `off_timeout_sec 5.0→15.0` 수정이 이 건인데 로봇에 미배포 상태
  2) 서버 15:41:03 EquipmentPluginLoaderService 플러그인 리로드로 설비 인스턴스 재생성 → 15:41:21 "Connector DISCONNECTED" 표시. AMR 끊김 아님
- AMR#2는 브로커에 state 자체가 안 올라옴 → 구독 결함 아니고 실물/네트워크 단절
