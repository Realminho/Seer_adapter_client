# diag-lastnodeid-missing-amr1

### 목표
- AMR #1(HN-SH6-TR-001, 192.168.101.61)이 LAST_NODE_ID_MISSING에서 못 벗어나는 근본 원인 규명
- nearestNodeId까지 비어 있는 이유 확인 후 복구/재발방지 방법 제시

### 지금
- 원인 확정 및 현재 정상화 확인 완료. 재발 방지 조치는 사용자 판단 대기

### 완료
- 코드 추적: `_refresh_last_node_id_errors`(adapter_jibot.py:2434)는 state를 매 cycle
  publish할 때만 갱신됨 → state가 발행되지 않으면 에러가 절대 안 지워짐
- 부분 배포 확인: `adapter_jibot.py`(14:47:39 갱신)는 `ErrorType.JIBOT_MOTOR_DISABLED`
  (:2503, :2562)를 참조하는데 배포된 `protocol/vda_2_0_0/vda5050_2_0_0_state.py`는
  mtime 2000-01-01(미갱신)이라 해당 enum 멤버가 없었음. 해당 멤버는 커밋 c3723f6에서 추가됨
- journal 증거: `publish_state()` Task가 첫 cycle(line 667 `_refresh_jibot_status_errors`)에서
  `AttributeError: type object 'ErrorType' has no attribute 'JIBOT_MOTOR_DISABLED'`로 즉사.
  PID 27386(14:50:38 기동), PID 27882(14:54:14 기동) 모두 동일
- asyncio Task 예외라 프로세스는 살아 있음: JIBOT TX/RX 폴링, instantActions 처리,
  connection=ONLINE 유지 → 겉으로는 정상, 실제로는 state 0건
- 브로커 실측(192.168.101.50:11883): 25초 구독 시 TR-001 state 0건 / TR-002 정상 발행
- nearestNodeId 빈 값은 "맵·pose가 아직 없던 부팅 구간 스냅샷"이 그대로 얼어붙은 결과.
  오늘 그 조건이 성립한 유일한 구간은 09:46:46~09:51:12 (JIBOT TCP 연결 실패로 pose 없음,
  UmGetMap 미수신). 09:51:08 맵 수신 → 09:51:12 `[LAST NODE FALLBACK] nearestNodeId=1_01CH gap=139.4`
- 15:06:13 전체 재배포/재시작 후 정상화 확인:
  state 발행 재개(25초 8건), lastNodeId=1_01CH, nearestNodeId=1_01CH, gap=98.1, errors 없음
- 배포본 adapter_jibot.py md5 = 로컬 작업트리와 동일(d3b24b8d…), 배포 enum에 JIBOT_MOTOR_DISABLED 존재
- nearestNodeId=1_01CH 정합성 확인: 맵상 1_01CH는 PathPoint이자 Dock으로 중복 존재,
  pathPoint 모드에서 현재 pose(10653,-2518) 기준 최근접(98.1, 차순위 p40 3067.5)

### 원인
- **부분 배포**. adapter_jibot.py만 갱신되고 protocol/ 패키지가 옛 버전으로 남아
  publish_state 코루틴이 첫 루프에서 AttributeError로 죽음 → 14:50:38~15:06:13 동안
  state 미발행 → FMS가 마지막 수신 state(부팅 구간 스냅샷)에 고정 → 에러 해소 불가

### 다음
- (선택) 재발 방지: publish_state 루프 본문 try/except + main.py에서 create_task에
  done-callback을 달아 Task 사망 시 로그/프로세스 종료 유도. 현재는 조용히 죽고
  connection=ONLINE이라 감지 불가

### 검증
- journal traceback(3개 PID) + 배포 파일 mtime/grep + 브로커 실측 구독 + 맵 좌표 재계산
  4중 대조로 원인 확정, 재배포 후 라이브 state로 정상화 확인
