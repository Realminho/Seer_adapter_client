# unclamp-action-not-executed

### 목표
- 62호기가 1_01CH 도착 시 order action `unclamp`이 실행되지 않은 원인 파악 및 수정
- 탈출 조건인 `clamp`이 실행되지 않은 원인 파악 및 수정
- action 실패 시 adaptor가 에러를 내도록 수정

### 지금
- unclamp 원인 하드웨어 확증 완료. adaptor 쪽 수정 3건 TDD로 완료(전체 스위트 통과)
- 남은 것: unclamp 목표 위치 config 실측값 확정 (사용자 확인 필요)

### 완료 — 정정
- 초기 "dock 실패 → node action 통째 skip" 가설은 이번 건 원인이 **아니었음**.
  로그상 dock은 성공(15:58:42 UmDock → 15:59:30 충전)했고 action도 dispatch됨

### 완료 — 원인 1: unclamp = 목표가 축 가동범위 밖 (하드웨어/설정)
- 62 = `HN-SH6-TR-002`(192.168.101.62). WiFi 플래핑으로 접속이 끊겼다 붙었다 함
- 로그: order 수신 정상 → `[ORDER ACTION] type=unclamp` dispatch 정상 →
  `[ORDER HARDWARE ACTION FAILED] ... motion never started within 1.0s ...
  the EZI drive appears to have ignored the command`
- **08-10 이후 dispatch 10회 / 실패 10회 / 성공 0회** (`CLAMP ACTION DONE` 0건)
- **드라이브 직접 확인(read-only probe)으로 원인 확정**:
  | 항목 | 값 |
  |---|---|
  | 드라이브 | `Ezi-SERVO2 ST Plus-E (V06.01.030.26)` — 응답 정상 |
  | 현재 엔코더 위치 | **0** |
  | `FFLAG_HWNEGALMT` | **true** (마이너스 하드웨어 리미트에 물려 있음) |
  | ERRORALL / EMGSTOP | false / false (알람 아님) |
- 즉 축이 **마이너스 리미트에 붙어 있는데 unclamp이 -16000으로 더 내려가라고 명령**함.
  드라이브는 패킷만 ACK하고 이동은 안 함 → 100% 실패
- -16000이 나온 경위: 배포 config에 `unclamp_position`/`unclamp_offset`/`origin_encoder`가
  모두 주석 → `_resolve_clamp_target`의 legacy 경로 `-origin_encoder_offset`(=-16000)로 떨어짐
- 참고: 저장소 루트 extensions.hcl에는 `clamp_position = 35000`이 살아 있어 실제 가동
  구간은 0~35000 근방의 **양수 구간**으로 보임 → 열림(unclamp)은 0 근처여야 함

### 완료 — 원인 2: clamp = 시작 노드가 통째로 버려짐 (adaptor)
- 탈출 order(20260812-327-1)에서 clamp는 **시작 노드**에 있음:
  `node seq=0 id=1_01CH actions=2` → `unclamp` + `clamp(trigger=BEFORE_LEAVE_NODE)`
- `_prune_v3_order_state_before_last_node`가 "이미 지나온 prefix"로 보고 그 노드를 제거
  → action도 함께 소멸 → `[ORDER ACTION] type=clamp` **전 기간 0건**
- 로봇은 clamp 없이 p39로 출발

### 완료 — adaptor 수정 4건 (TDD, 테스트 먼저 실패 확인)
0. **legacy fallback 제거**: `_resolve_clamp_target`의 `±origin_encoder_offset` 폴백 삭제.
   `*_position`도 `*_offset`도 action의 `position`도 없으면 **ValueError로 실패**하며
   메시지에 빠진 키 이름과 `extensions.hcl`을 적는다. servo를 켜기 전에 실패하므로
   축에 전류도 물리지 않는다. 기존 폴백을 검증하던
   `test_clamp_falls_back_to_origin_encoder_offset`는 새 계약 테스트로 교체

1. **시작 노드 action 실행**: `OrderStep.actions_only` 추가. 이미 서 있는 시작 노드는
   주행은 건너뛰되 action은 실행. prune이 그 노드를 남기고 queue가 actions_only로 넣음
   (`[ORDER STEP ACTIONS ONLY]` 로그). dock 노드를 다시 UmDock 하지 않는 것도 테스트로 고정
2. **실패 시 에러 발행**: `ErrorType.ORDER_ACTION_FAILED` 추가.
   `_execute_order_action`을 wrapper로 만들어 모든 dispatch 분기의 FAILED를 한 곳에서
   FATAL 에러로 올림(actionId 단위 dedupe). 기존 본문은 `_dispatch_order_action`으로 분리
3. **예외 유출 차단**: registry/recipe handler가 raise하면 그대로 스케줄러로 새어
   actionState가 RUNNING에 고착되던 것을 FAILED + 에러로 전환
- 추가: "드라이브가 명령을 무시" 에러에 `target=… actual=… limitsActive=…`를 붙여
  이번 같은 원인이 로그만으로 즉시 보이게 함

### 검증
- 신규 테스트 4건 모두 red→green 확인
- `tests.test_adapter_jibot_v3_order` 652건: 신규 4건 통과, 실패는 기존 PIO 타이밍 6건뿐
- 전체 스위트 1430건 실행: 동일하게 PIO 6건만 실패(내 변경 전 baseline과 같음).
  discovery 시 뜬 config 로드 error 5건은 cwd 아티팩트 — 정상 cwd에서 재실행 시 모두 OK

### 다음
- config 값은 **쓰지 않았다**(사용자 지시). 대신 없으면 실패하도록 바꿨으므로,
  62에 배포하면 clamp/unclamp이 `unclamp_position is not configured …` 로 즉시 실패한다.
  현장에서 실측 엔코더 값을 `extensions.hcl`에 채워야 동작한다
- 주석만 갱신함(값 아님): `adaptor/config/extensions.hcl`,
  `adaptor/config/extensions.hcl.example`, 루트 `extensions.hcl` — 폴백이 사라졌고
  이제 필수 항목임을 명시. 예시 숫자(-16000 등)는 오해 소지가 커 `<실측>`으로 교체
- 배포는 `scripts/update-jibot-adapter-over-ssh.sh`로 62에 반영 (아직 안 함)
