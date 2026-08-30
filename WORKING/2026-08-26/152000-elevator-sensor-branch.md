# elevator-sensor-branch

### 목표
- 엘리베이터 문 열기 recipe를 센서 판정으로 전환. 1층: PIO in2(카가 상층)가 on이면 층 호출, off면 문 열기. 두 갈래 모두 open sensor(in1) 확인 후 완료.
- 2층도 같은 구조. open sensor 번호는 현장 확인 후 확정.

### 지금
- 구현·테스트 완료. 2층 입력 번호 실측 대기.

### 완료
- extensions/pio: pioScenario에 `if` 단계 추가(choose_pio_branch). 조건 입력 1회 읽기, then/otherwise, 중첩 금지, 읽기 실패 시 FAIL.
- recipes.hcl: pioElevatorOpen1f/2f를 pioScenario 1개로 재작성. timeout 60 → 180. cleanup에서 양쪽 출력 모두 off.
- extensions.hcl: input_signals에 elevator1fOpened/1fCarUpper(=1/2), elevator2fOpened/2fCarLower(추정, 현장 확인 필요) 추가.
- 테스트: pio if 단계 4건, recipe acceptance 갱신+신규 1건, io_simulator 갱신+신규 1건, recipes_config pulse 검사 갱신.

### 다음
- 2층 station(000020)의 문 열림/카 위치 입력 번호 실측 → extensions.hcl 두 줄만 수정.
- 1층 in2 극성(on = 카가 상층) 현장 확인.
- 닫기(pioElevatorClose*)·층 호출(pioElevatorMove*)도 같은 방식으로 센서 판정 전환 검토.

### 검증
- `./scripts/run-tests.sh -q` 관련 파일 통과. 남은 실패 5건(airshower 계열 + close pulse 7초)은 HEAD 워크트리에서도 동일하게 실패하는 기존 건이라 이 작업과 무관.
