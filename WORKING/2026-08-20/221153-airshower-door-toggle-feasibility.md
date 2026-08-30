# airshower-door-toggle-feasibility

### 목표
- (1) 에어샤워 통과 recipe 4단계 x 방향 2개 + blink 스텝 구현
- (2) 전체 테스트 실패 8건 수정

### 지금
- 전체 스위트 재실행 중

### 완료 (1) 에어샤워
- extensions/pio: pio_blink_output() + pioScenario "blink" 스텝(untilIndex/untilState, count, timeoutSec),
  "out" 스텝의 signal 지원, pair 파라미터(기본 true), disconnect를 parse_pio_flag로 읽음(bool("off") 버그)
- extensions/pio/panel.html: $field_pioScenario_pair / _disconnect
- config/extensions.hcl: output_signals에 airShower3lOpen=1, airShower4lOpen=2
- config/recipes.hcl: airShower{3l-4l,4l-3l}{OpenIn,PassIn,OpenOut,PassOut} 8개 + airShowerRelease
- tests/test_pio_scenario_blink.py 8건, test_recipes_config.py 3건 신규

### 완료 (2) 테스트 실패 8건
- test_config sound replay gap: 배포 config.toml이 fault=3.0을 선언 → 기대값 {} 고정을 걷고
  {소문자 토큰: float} 파싱만 검사 (조회 규칙은 test_jibot_bumper_errors가 검사)
- test_systemd 2건: shutil.which를 고정 안 해서 systemctl 없는 macOS에서 [] 조기반환.
  merge 시험은 실패했고, "실패/타임아웃" 시험은 통과했지만 사실상 빈 검사였음 → 둘 다 which 고정
- test_fleet_registry: macOS /var -> /private/var 심볼릭 링크. 기대값도 .resolve()
- test_pio_output_mapping / test_recipe_acceptance x2 / test_recipes_config x2:
  엘리베이터 신호가 층별로 갈라진(elevator1fOpen 등) 변경이 HEAD(c9bfeca)에 들어왔는데 테스트가 안 따라옴
  - 1층: out1/out2=층 호출, out3/out4=문 / 상층: out1/out2=문, out3/out4=층 호출
  - extension "elevator"의 open/close_door_pin은 **1층 배선만** 담음 → 1f만 대조 가능
  - 상층은 열기!=닫기, station당 네 신호 전부 distinct 라는 구조 검사로 대체
  - pulse 폭 실제값 반영: 문 1s/1s, 층 호출 10s(1f-1f만 11s)/1s
- 낡은 주석 정리: recipes.hcl 헤더 예시(elevatorOpen -> elevator1fOpen), "out 번호는 도착층" 규칙 삭제,
  extensions.hcl의 open/close_door_pin에 "1층 배선" 명시

### 다음
- 현장 확인: sensor3l=in1 / sensor4l=in2 추측값, 점유=in3 검증, OFF가 닫기인지, 세정 대기 600s 실측
- 확인 요청: pioElevatorMove1f-1f의 on 지연 11초가 의도인지(나머지 셋은 10초)

### 검증
- 대상 테스트 국소 실행 전부 통과. 전체 스위트 재실행 중
