# check-unit2-ezio

### 목표
- 2호기 EZIO 값이 잘못 표시되는 원인을 확인한다.

### 지금
- PIO와 EZI IO 번호 체계를 모두 0~7로 통일하는 수정을 완료하고 검증 중이다.

### 완료
- EZIO 출력 값이 한 칸 어긋나는 원인은 기존 `output_bit()`의 `15+n` 쓰기 비트와 `16+n` 읽기 비트 불일치다.
- 현재 작업트리에는 `16+n`으로 수정된 코드와 회귀 테스트가 이미 있다.
- 코드상 입력 n→입력 비트 n, 출력 n→출력 비트 16+n으로 동일한 논리 핀 n을 쓰고 읽는 1:1 매핑이다.
- 단, 이 수정은 아직 git 미커밋 상태이므로 2호기 실장비에 적용됐다고 볼 수 없다.
- 로컬 2호기 inventory는 simulator이며 `ezi_io`가 주석 처리되어 있어 실장비 값을 만들지 않는다.
- 추정 2호기 호스트 `192.168.3.223` 진단은 SSH timeout으로 원격 상태를 확인하지 못했다.
- 현재 저장소의 recipe 이름은 `ezioElevatorOpen`이 아니라 `pioElevatorOpen`이다.
- `pioElevatorOpen`은 `pioInit` 성공 후 `elevatorOpen` 신호(EZI IO out4)를 0.2초 켰다가 끈다.
- 이 recipe는 문 열림 입력을 확인하지 않으므로 출력 펄스 성공만으로 FINISHED가 된다.
- 2호기 snapshot의 `pio.connected=false` 상태라면 `pioInit`에서 실패해 문 열림 출력까지 진행하지 못할 수 있다.
- 기존 elevator workflow도 `open_door_pin` 설정값을 그대로 `turn_on_output()`에 넘기며, 저장소 설정은 `open_door_pin = 4`다.
- 따라서 기존 workflow와 새 recipe 모두 내부 0-based EZI 핀 index 4(물리/PIO 관점의 다섯 번째 출력)를 사용하도록 맞춰져 있다.
- 최종 현장 번호 체계를 확인했다: PIO는 1~8, EZI IO는 0~15이며 PIO N→EZI N-1이다.
- PIO 입력·출력 파서, 설정 검증, ping, WebUI 표시를 1~8로 복구했다.
- 기본 순차 배선은 `output_pins`/`input_pins = [0..7]`로 통일하고 `output_pin_map`은 비순차 배선용 선택 기능으로만 남겼다.
- 열림/닫힘 번호는 코드 상수가 아니라 `output_signals`와 기존 workflow의 door pin 설정으로 변경 가능하며, 열림=5 설정 회귀 테스트도 추가했다.
- SELECT 해제 정책을 `select_off_timing`으로 설정 가능하게 했다. 기본 `after_go`는 GO 확인까지 SELECT를 유지하고, `after_bc`는 기존 BC별 토글 방식이다.
- `select_off_delay_sec`으로 GO 확인 후 SELECT OFF까지 추가 유지 시간을 설정할 수 있다.
- 1호기 실기에서 `after_go`도 30초/14회 후 GO 판정에 실패했다. 설비가 SELECT falling edge로 pairing을 확정할 가능성이 있어 `after_bc` 경로에도 `select_off_delay_sec`이 적용되도록 누락을 수정했다.
- 현장 handshake를 재확인해 연결 성공 조건을 설정으로 분리했다. 기본 `pair_confirmation="bc_reply"`는 유효 BC 응답 후 SELECT OFF 즉시 성공하며 GO를 기다리지 않는다. 다른 설비는 `"go"`를 선택할 수 있다.

### 다음
- 각 로봇의 보존 `extensions.hcl`에 열림/닫힘 PIO 신호 번호를 설정하고 재배포한다.
- 실행 시 설정된 PIO N이 EZI N-1에 펄스를 내는지 확인한다.
- 1호기에 새 pairing 코드를 배포하고 `after_go`에서 깜박임 없이 GO가 판정되는지 확인한다.

### 검증
- `test_ezi_io_output_bits.py` 5개 통과.
- 함께 실행한 snapshot 테스트 14개는 코드 실패가 아니라 현재 Python에 `hcl2`가 없어 import 단계에서 실패.
- 변경 Python 파일 `compileall` 및 `git diff --check` 통과.
- PIO/recipe 집중 pytest는 현재 실행 환경에 `hcl2`가 없어 수집 단계에서 실행하지 못했다.
- SELECT/연결 판정 단위 테스트 3개(`after_go`, `after_bc`, BC 응답만으로 성공) 통과.
