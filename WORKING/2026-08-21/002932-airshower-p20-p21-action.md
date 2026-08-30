# airshower-p20-p21-action

### 목표
- milk-run air-shower3-4: p17->p20 진입은 정상, p20->p21 진출이 안 되는 원인 확인 (진단만, 수정 없음)

### 지금
- D 유지 확정. 가설을 배포 recipe 파라미터로 기계적으로 검증함(아래).
- 전체 스위트 재실행 중. 남은 것은 현장 로그 1줄로 실제 발생 확인

### 정정 — FAILED HARD 액션은 결국 오더를 끝낸다
- 어댑터 내부 큐는 안 멈추지만, _execute_order_action이 **FATAL** ORDER_ACTION_FAILED
  에러를 state에 올리고(adapter_jibot.py:4531 _set_order_action_failed_error) FMS가 그걸 보고
  "남은 경로 주행을 중지하고 order를 종결"한다. 실제 FMS 메시지로 확인됨.
- 따라서 아래 "오더를 멈추지 않는다"는 어댑터 큐 한정이고, 운영상으로는 멈춘다.
- **결과**: A가 실배포에 살아 있었다면 p17의 OpenIn(HARD)에서 오더가 끝났어야 한다.
  p20까지 갔다면 그때 로봇의 recipes.hcl에는 `in` 단계가 없었다는 뜻이다(A는 잠재 결함).
  단, 그 FMS 에러가 p17의 것이라면 반대다 — 노드가 확인돼야 갈린다.

### 완료 — 전제 확인 (어댑터 큐 한정)
- FAILED order action은 어댑터 큐를 멈추지 않는다:
  registry.execute()가 예외를 ActionResult(FAILED)로 바꾸고(core/action_registry.py:316-319),
  _execute_registered_order_action은 상태만 기록하며, _process_v3_step_actions는 항상 True를 돌려준다.
  => HARD 액션이 실패해도 로봇은 계속 간다. 아래 A와 B는 동시에 살아 있을 수 있다.
- 이 airShower 8개 recipe는 전부 uncommitted (HEAD에는 airShowerPassage만 있음).
- 배포 스크립트의 --recipes-hcl-mode / --extensions-hcl-mode 기본값은 **keep**이다.
  파이썬 코드는 항상 올라가지만 recipes.hcl은 --configure-device로 덮어쓴 시점의 스냅샷일 수 있다
  => 로봇에 실제로 어떤 recipes.hcl이 있는지가 A의 성립 여부를 가른다.

### 완료 — 결함

A. pioScenario "in" 단계가 signal을 못 읽는다 → Open* 4개가 마지막 단계에서 항상 실패 (재현 완료)
   extensions/pio/__init__.py execute_pio_scenario: `parse_pio_index(step.get("index"))`.
   signal 해석(resolve_pio_output_index)은 out/blink에만 있다.
   recipes.hcl의 OpenIn/OpenOut 4개는 전부 `{ type="in", signal="doorSensor3l|4l" }`로 끝남
   -> ValueError "PIO index must be an integer from 1 to 8".
   앞의 out/delay/out(2초 pulse)은 이미 나간 뒤라 문은 열린다 -> "문은 열리는데 액션은 FAILED".
   진출 쪽이 더 아픈 이유: OpenOut의 `in` 대기는 **에어 세정이 끝나 반대편 문이 열렸는지** 확인하는
   유일한 관문(timeout 660초)이다. 이게 죽으면 세정 대기 없이 즉시 다음으로 넘어간다.
   진입 쪽은 애초에 기다릴 게 없어 같은 결함이 증상으로 안 보인다.
   부수: doorSensor3l/4l이 extensions.hcl의 **output_signals**에 선언돼 있다(입력 이름 맵이 없음).

B. blink가 첫 펄스 전에 종료조건을 먼저 본다 → PassOut이 0회 깜박이고 "성공" 보고 (재현 완료)
   pio_blink_output while 루프 첫 줄이 `if await _reached()`.
   PassOut = untilIndex 3(occupied = EZI in2), untilState "off".
   occupied는 utils/airshower.py handle_waiting_for_vacancy가 "**남이** 점유 중"으로 쓰는 입력이다
   (들어가기 전에 OFF 될 때까지 대기). 우리 로봇이 안에 있을 때 ON 되는지는 미검증(08-20 노트).
   ON이 안 되면 진출 blink는 즉시 stopped by condition / cycles=0 / 출력 한 번도 안 켬
   -> 나가는 동안 문을 전혀 잡지 않는다.
   (사용자 보고대로 p17에서 4l-3lPassOut을 썼다면 진입 blink도 똑같이 0회였을 것이다.
    진입은 2초 pulse만으로 통과가 됐고 진출은 잡아 줘야 해서 여기서만 티가 난다.)

C. 4l-3l PassIn/PassOut의 timeoutSec = 30, count = 60 (1초/회) -> count 도달 불가, 항상 30초 timeout.
   blink timeout은 ok=False = FAILED. 3l-4l 쌍은 90초. 복붙 누락으로 보인다.

D. (추정) PassOut cleanup의 pioDisconnect가 pairing을 푼다. cleanup은 성공/실패 무관 항상 실행
   (extensions/recipes/__init__.py:131). p17에서 4l-3lPassOut을 썼다면 진입 직후 pairing이 끊긴다.
   OpenOut 계열은 pair=false에 stationId/channel도 없어 다시 걸 수 없다(pio_link_params가 raise).
   EZI 출력 자체는 pairing 없이도 나가므로 코드상 실패는 아니다. 다만 out1/out2는 엘리베이터
   층 호출과 같은 EZI 핀(output_pin_map 1=0, 2=1)이라 어느 설비가 듣느냐를 pairing이 정한다는
   추론. 설비 동작 확인 필요.

### 완료 — A 수정 (TDD)
- config/config.py: PioConfig.input_signals 추가. _signal_map(label=)로 일반화해
  오류 문구가 output_signals/input_signals를 구분한다.
- extensions/pio/__init__.py:
  pio_input_signal_index / resolve_pio_input_index / known_input_signals 추가
  (_pio_named_index, _resolve_pio_index로 출력 쪽과 규칙 공유),
  execute_pio_scenario의 "in" 분기가 parse_pio_index(step["index"]) -> resolve_pio_input_index(step)
- config/extensions.hcl: doorSensor3l/4l을 output_signals -> 새 input_signals 맵으로 옮김
- config/recipes.hcl: 낡은 주석("입력에는 이름 맵이 없다") 갱신
- tests/test_pio_input_mapping.py 신규 17건
- **배포 주의**: 파이썬 코드와 config를 반드시 **같이** 올려야 한다. 배포 스크립트는
  파일별 모드가 따로라 절반만 적용되는 게 기본 경로다.
    extensions.hcl만 가면: input_signals를 모르는 코드 + output_signals에서 사라진 doorSensor*
    코드만 가면: input_signals가 비어 모든 in 단계가 "is not declared"로 실패
  => --extensions-hcl-mode overwrite --recipes-hcl-mode overwrite (또는 --configure-device)

### 완료 — B/C/D 수정 (TDD)
B. blink에 minCycles 추가(기본 1). 조건이 처음부터 참이어도 첫 사이클은 온전히 돈다.
   - pio_blink_output(min_cycles=), _hold(interruptible=)로 채울 사이클은 조건이 못 자른다
   - 시작 시점 조건은 **기록용으로만** 한 번 읽어 conditionTrueAtStart로 남기고,
     message 끝에 "[condition already true at start]"를 붙인다.
     안 그러면 "처음부터 참"과 "깜박이다 참이 됨"이 같은 문자열이 되어
     in3이 틀렸다는 사실이 로그에서 지워진다.
   - parse_blink_min_cycles(step["minCycles"])
   - blink를 쓰는 recipe는 airShower Pass* 4개뿐임을 확인하고 기본값 1을 정함
   - **주의: 이것만으로 현장 증상이 낫지 않는다.** 진출 구간은 통과하는 내내 문을
     잡아야 하는데 1초 pulse 한 번은 여전히 부족하다. in3 실측 후 minCycles를 올려야 한다.
C. blink timeoutSec를 count 예산보다 크게. 3l-4l 30→45(count 30), 4l-3l 30→90(count 60).
   짧으면 count에 절대 못 닿고 늘 timeout=FAILED라, 설비 무응답과 설정 오류가 구분이 안 된다.
   (count가 방향별로 30/60으로 다른 건 그대로 뒀다 — 의도인지 확인 필요)
D. OpenIn/OpenOut 전부 stationId/channel을 갖고 스스로 pairing한다(OpenOut의 pair=false 제거).
   근거는 "설비가 unpaired 요청을 무시한다"(미검증)가 아니라 구조다: FMS가 4단계를
   순서대로 걸어 준다는 보장이 없는데 OpenOut은 앞 액션이 남긴 pairing에 기대고 있었고,
   어긋나도 감지할 방법이 없었다. HARD 진입점이 스스로 성립하면 이 부류가 사라진다.
   SELECT 토글 0.7~2.7초는 감수 — OpenOut 시점엔 깜박이는 중이 아니다(앞의 NONE 액션이
   끝나야 HARD가 돈다. 그 "끝"은 cleanup까지라 문 출력도 이미 내려가 있다).
- tests/test_airshower_passage_recipes.py 신규 3건, test_pio_scenario_blink.py +5건
- recipes.hcl 헤더 주석 갱신(pairing 규칙, input_signals, timeoutSec 관계, minCycles)

### 철회 — "노드가 한 칸 늦다"는 내 진단은 틀렸다
사용자 확인: 표의 "p21"은 node p21이 아니라 **edge p20->p21**이다(FMS UI가 도착
노드 이름으로 표시). edge 액션은 주행 **전에** 돈다(adapter_jibot.py:4231-4236)
-> 타이밍은 정상이고 설계 의도대로다. 아래 옛 진단은 무효.

### 유력 원인 — pairing이 진입 구간에서 끊긴다 (= 결함 D 그 자체)
실제 배치(edge 기준):
  edge p17->p20 : airShower3l-4lOpenIn (HARD) + airShower4l-3lPassOut (NONE)
  edge p20->p21 : airShower3l-4lOpenOut(HARD) + airShower3l-4lPassOut (NONE)

1. OpenIn은 stationId=000030/channel=250을 갖고 **pairing을 건다**. 3L 문 열림 = 정상.
   => 이 현장에서 BC pairing은 잘 된다(앞서 "pairing 없이도 문이 열린다"고 본 내 추론은
      p17이 OpenOut인 줄 알았기 때문이다. OpenIn이었으므로 근거가 사라졌다).
2. 같은 edge의 airShower4l-3lPassOut은 cleanup에 **pioDisconnect**가 있다.
   cleanup은 성공·실패 무관 항상 돈다 -> 진입 직후 unpair + 시리얼 포트 close.
3. edge p20->p21의 airShower3l-4lOpenOut은 배포판에서 pair=false에 stationId도 없다
   -> 다시 걸 방법이 없다. 4L 문 요청이 EZI로 나가지만 듣는 설비가 없다.
4. 문이 안 열리므로 문 열림 확인 단계가 60초 timeout -> HARD FAILED -> FMS가 order 종결.

=> "4L door 오픈부터 안 된다"와 "HARD blocking 액션 실패" 두 증상이 **한 원인**으로 설명된다.
=> 따라서 D 수정(OpenOut이 stationId/channel을 갖고 스스로 pairing)은 되돌리면 안 된다.
   이게 바로 그 고장이다. 반대 방향(p21->p20->p17)도 같은 구조다.

보조 원인: 진입 구간에 PassOut(진출용)이 걸려 있다. 여기에는 airShower3l-4lPassIn이 맞다
  - PassIn에는 pioDisconnect cleanup이 없다 -> FMS만 고쳐도 pairing이 안 끊긴다
  - untilState도 진입은 "on"이 맞다(PassOut은 "off"라 0회 깜박임 = 결함 B)

검증 완료 (tests/test_airshower_pairing_handover.py, 배포 recipe 파라미터를 그대로 사용):
  1. airShower3l-4lOpenIn -> result["init"] is not None       = pairing 건다
  2. airShower4l-3lPassOut의 cleanup에 pioDisconnect 있음      = 진입 구간에서 끊는다
     (airShower3l-4lPassIn에는 없음 -> FMS만 고쳐도 해결됨을 같이 고정)
  3. OpenIn -> pioDisconnect -> OpenOut 순서로 돌리면
     현재(D 수정 후): init is not None = 다시 건다
     예전 모양(pair=false, stationId 없음): init is None, 그런데 ok=True로 **성공 보고**
     -> 문 출력은 나가고 recipe는 FINISHED. 실패로 보이지 않는 것이 이 고장의 성질.
  RED 확인: recipes.hcl의 OpenOut을 예전 모양으로 되돌리면 3번이
  "AssertionError: unexpectedly None : OpenOut이 pairing을 다시 걸지 못했다"로 깨진다.

남은 확인 — 현장 로그(둘 중 하나가 찍혀 있어야 한다):
  [ORDER ACTION ERROR] ... type=airShower3l-4lOpenOut: ... expected in2=on within 60.0s
  [ORDER ACTION BLOCK FAILED] Could not stop automatic driving before action: ...

### (무효) 옛 진단 — FMS 액션 배치가 원인
사용자가 준 실제 FMS 설정:
  p21: airShower3l-4lOpenOut (HARD) + airShower3l-4lPassOut (NONE)
  p17: airShower4l-3lOpenOut (HARD) + airShower4l-3lPassOut (NONE)
  p20: **액션 없음**

지도: p17(76793) - p20(79382) - p21(82178). p20이 에어샤워 안이다.

문제 1 — 나가는 쪽 문 액션이 한 노드 늦게 걸려 있다.
  node 액션은 **그 노드에 도착한 뒤에** 돈다(adapter_jibot.py:4238-4242.
  edge 액션이라야 주행 전에 돈다). 3L->4L 진행에서 4L 문을 여는
  airShower3l-4lOpenOut이 p21에 걸려 있으므로, 로봇은 그 문을 아직 안 연 채로
  p20->p21을 주행해야 도착할 수 있다. 문이 막고 있으면 도착 자체를 못 한다.
  => "p20 -> p21이 안 된다" / "4L door 오픈부터 안 된다"와 정확히 일치.
  들어가는 쪽(p17)은 도착 후 3L 문을 열면 되므로 타이밍이 맞아 잘 된다.
  반대 방향(p21->p20->p17)도 같은 구조로 3L 문이 늦는다.

문제 2 — OpenIn/PassIn을 아무 데도 안 쓴다. 양 끝에 OpenOut/PassOut만 걸려 있다.
  PassOut은 untilState="off"(진출용)이라 진입 자리에서는 조건이 처음부터 참이라
  0회 깜박인다(결함 B). 진입 자리에는 PassIn(untilState="on")이 맞다.

의도한 배치 (recipes.hcl 헤더의 4단계):
  p17 -> p20 -> p21 (3L->4L):
    p17: airShower3l-4lOpenIn (HARD) + airShower3l-4lPassIn  (NONE)
    p20: airShower3l-4lOpenOut(HARD) + airShower3l-4lPassOut (NONE)
  p21 -> p20 -> p17 (4L->3L):
    p21: airShower4l-3lOpenIn (HARD) + airShower4l-3lPassIn  (NONE)
    p20: airShower4l-3lOpenOut(HARD) + airShower4l-3lPassOut (NONE)

확인 필요: FMS UI가 edge 액션을 끝 노드 이름으로 표시하는지. edge(p20->p21)
액션이면 주행 전에 돌아 타이밍이 맞으므로 문제 1은 성립하지 않는다.

### D 재검토 — 전제에 반하는 증거
- 배포판 OpenOut은 pair = false에 stationId/channel도 없다(= pairing 못 건다).
  그런데 이 설정에서 오더의 첫 에어샤워 액션이 p17의 airShower4l-3lOpenOut이고,
  사용자는 진입(3L 문)은 잘 된다고 한다.
  => 설비가 pairing 없이 EZI 출력만으로 문을 연다는 정황 증거다.
- 그렇다면 D 수정(OpenOut이 직접 pairing)은 기능상 이득이 없고
  BC timeout/GO 미상승이라는 **새 HARD 실패 경로**만 추가한다(= order 종결).
  구조적 근거는 남지만 실익 대비 위험이 커졌다. 배포 전 사용자 판단 필요.
  (대안: pairing 실패를 치명적으로 다루지 않거나, D를 되돌린다)

### 이전 기록 — FMS 액션 배치가 잘못됐을 가능성
- 사용자 보고: p17(진입)에 airShower3l-4lOpenIn + **airShower4l-3lPassOut**
- PassOut은 진출용이다(untilState="off"). 진입에는 3l-4lPassIn(untilState="on")이 맞다.
  출력 핀은 우연히 같지만(둘 다 airShower3lOpen) 종료 semantics가 반대다.
- 게다가 PassOut cleanup의 pioDisconnect가 진입 직후 pairing을 끊는다 —
  FMS를 3l-4lPassIn으로 고치면 D의 전제 자체가 사라진다.
- 의도한 4단계: node A: 3l-4lOpenIn(HARD) + 3l-4lPassIn(NONE)
                node B: 3l-4lOpenOut(HARD) + 3l-4lPassOut(NONE)
  => FMS의 p17/p20 액션 목록을 이것과 대조해야 함

### 배포 위험 — A 수정의 부작용 (배포 전 확인 필요)
- A 수정으로 `in` 단계가 **실제로 돌기 시작한다**. doorSensor3l=in1 / doorSensor4l=in2는
  아직 추측값이다(현장 확인 필요라고 config에 적혀 있음).
  핀이 틀리면 Open*이 "expected in1=on within 60.0s"로 FAILED -> HARD -> 오더 종결.
  즉 A 수정 전에는 늘 ValueError로 죽던 자리가, 수정 후엔 핀이 맞아야만 산다.
- D 수정으로 OpenOut이 pairing을 직접 건다 -> BC timeout / GO 미상승이 새 HARD 실패 경로가 된다.
  조용히 엉뚱한 출력을 내보내는 것보다 낫지만, 오더가 서는 빈도는 늘 수 있다.
- => in1/in2를 먼저 실측하고 배포하는 편이 안전함.

### 다음
- 이번 FMS 에러 확인법: 로봇 로그에서
  `[ORDER ACTION ERROR] actionId=01a01fef-...:01a01fbe-...:0`
  이 줄에 type=<recipe 이름>과 전체 reason이 같이 찍힌다(adapter_jibot.py:4575).
  FMS 쪽이면 그 에러의 errorReferences에 actionType / nodeId / reason이 들어 있다.
- 사용자 확인 필요 (p17, p20 **둘 다**):
  1) [ORDER ACTION] 줄 — action 이름 / owner가 node=p20인지 edge=p20->p21인지 / blockingType
  2) Open* 결과 message에 "PIO index must be an integer from 1 to 8"이 있는가
     -> 있으면 A가 실배포에 살아 있음 / 없으면 로봇의 recipes.hcl이 `in` 단계 이전 스냅샷 = A는 잠재 결함
  3) blink 결과 message "out{N} blinked {M} time(s), stopped by {condition|count|timeout}"
     -> p20에서 "blinked 0 time(s), stopped by condition"이면 B 확정
- 수정은 사용자 결정 후. 후보: in 단계 signal 지원 + input_signals 분리, blink 첫 사이클 강제,
  4l-3l timeoutSec 90 통일, PassOut cleanup의 pioDisconnect 재검토.

### 검증
- 전체 스위트 1598건 통과 (unittest discover, EXIT=0)
- A(in+signal ValueError), B(blink 0회 단축 종료) 두 건 모두 tests 스텁(_Ezi/_adapter)로 재현함
- C/D는 배포 config를 읽는 가드 테스트로 RED 확인 후 수정
- "FAILED가 오더를 안 멈춘다"는 코드 경로 읽기로 확인(위 전제 참조)
