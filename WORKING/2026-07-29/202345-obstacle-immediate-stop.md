# obstacle-immediate-stop

### 목표
- order `20260729-203` 실행 중 장애물 감지로 즉시 정지하는 현상의 근본 원인 파악

### 지금
- serial `HN-SH6-TR-001` 토픽을 두 시스템이 공유하는 것을 확인. 실제 정지 시점의
  raw `jibotStatus` 확보만 남음

### 완료
- 어댑터는 장애물로 order를 중단하지 않음을 코드로 확인
  (`adapter_jibot.py:3836` 노드 대기 무기한, `um_stop`은 startPause(5226)/
  cancelOrder(5688)/manual-drive 경로에서만 호출)
- "장애물 감지" 음성 출처 = 로봇 펌웨어가 아니라 **우리 어댑터의 brake.mp3**
  - TR-001 JIBOT config `info-sound.play_warning_voice = false` (펌웨어 음성 꺼져 있음)
  - `adapter_jibot.py:1254` detail=="BRAKE"이면 brake.mp3 루프 (`sounds/brake.mp3` 존재)
  - BRAKE = `fieldViolation && !driving` = JIBOT status에 `#brake` (`553`,`939`,`1134`)
- **핵심: 같은 serial `HN-SH6-TR-001`을 서로 다른 두 구현이 동시에 쓰고 있음**
  - 20:31~20:39 = 우리 어댑터: `manufacturer=jibot`, `information`(단수), `agvPosition`,
    jibotMode/jibotStatus 채워짐, lastNodeId=p39
  - 21:53~현재 = 타 구현: `manufacturer=l2m`, `map=FABRIS`, `informations`(복수),
    `mobileRobotPosition`, `zoneActionStates`, battery 100%, lastNodeId=p40 고정
  - `git log -S '"informations"' --all` → 결과 없음. 우리 코드가 낸 적 없는 키
- 관측된 TR-001 order 흐름 (MQTT 녹화, `scratchpad/incident.jsonl`)
  - 20:36:02 cancelOrder → 20:36:07 order `20260729-205-1-1` [p39, 1_01CH] 접수
  - 접수 후 2분간 driving=false, jibotStatus=Stopped — 로봇이 출발조차 안 함
  - 20:38:06 state 전체 공백(orderId='', jibot 필드 '', LAST_NODE_ID_MISSING)
  - 20:38:22 cancelOrder → 20:39:38 order `20260729-206-1-1` 동일 노드 재시도
  - 203/205/206 연속 재시도 = 사용자가 말한 "계속 발생"
- 라이브 JIBOT query(초기): .61/.62 모두 obs=false, path num=0, CurTask cmd=stop
- JIBOT 내부 config는 .61/.62가 dock/sound 빼고 동일 → 로봇별 회피 튜닝 차이 아님
- 22:0x 현재 .61/.62 둘 다 ping/7273 unreachable, MQTT에는 l2m publisher만 살아있음

- serial 출처 확인: 우리 어댑터는 **이미 robots.hcl 단일 출처**
  - `fleet.py:8,35` `robot "<id>"` 라벨 -> `vehicle.serial_number` -> 토픽 prefix(`registry.py:98,312`)
  - `config.toml [vehicle]`에 serial_number 키 없음(dataclass 기본 `""`)
  - 비면 `main.py:665`가 부팅 차단. 우회로는 `--id/--serial-number` 디버그 플래그뿐(`main.py:120`)
- 충돌 상대는 ACS/FABRIS 쪽으로 보임: `l2m`은 eq 클래스명
  `packages/amr/src/lib/amr/amr-l2m-v3/amr-l2m-v3-base.equipment.ts`
- `informations`는 VDA5050 v3 스키마(`vda5050_v3/json_schemas/state.schema`)에도 없는 키

### 다음
- serial 중복 해소는 **ACS/FABRIS 쪽 작업**: 시뮬레이션 AMR이 실차와 같은
  `HN-SH6-TR-001`로 붙지 않도록 분리 (양쪽이 같은 `/order`,`/instantActions`를 구독)
- 브로커에 `HN-SH6-TR-01`(TR-001과 다른 serial)도 존재 — 네이밍 정리 필요
- 실제 로봇 복구 후 재현 시 정지 직전 raw `jibotStatus`에 `#brake`가 실제로 있는지 확인
  → 있으면 JIBOT 센싱/clearance 문제, 없으면 cancelOrder에 의한 정지
- ssh-agent 키 등록되면 로봇 journal에서 어댑터 재시작 사유 확보

### 검증
- `who_publishes.py`: TR-001 state가 mfr=l2m, map=FABRIS, informations 키로만 수신됨
- `tr001-raw-state.json`: `informations: []`, `mobileRobotPosition`, `zoneActionStates` 확인
- `grep -n '"information"' adaptor/protocol/vda5050_3_0/messages.py:579` → 우리는 단수 키
- `incident.jsonl` 51줄: order 205/206 접수 후 driving=false 유지, 20:38:06 state 공백
