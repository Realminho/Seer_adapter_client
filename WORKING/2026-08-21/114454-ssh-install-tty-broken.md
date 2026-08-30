# ssh-install-tty-broken

### 목표
- `update-jibot-adapter-over-ssh.sh --restart` 실행 시 콘솔 계단현상(indent 누적) + sudo 비밀번호 단계에서 멈추는 문제 해결

### 지금
- 수정 완료, 사용자 재실행 대기

### 완료
- 원인1(계단현상): `ControlMaster/ControlPersist` + `ssh -tt` 조합에서 mux 클라이언트가 로컬 raw termios 를 원격 pty 로 전달 → 원격 pty 가 `-onlcr -icanon -echo`
  - pty 하네스 재현: ControlMaster 없음 → `onlcr ... \r\n`, ControlMaster 있음 → `-onlcr ... \n`
- 원인2(멈춤): 로봇 `/etc/sudoers.d/adaptor-tui`(2026-08-10 생성) 에 `amr-xboxdrv.service` 없음 → 세 번째 유닛에서 대화형 sudo 비밀번호 요구. 게다가 pty 가 `-icanon/-echo` 라 입력이 정상 동작하지 않음
- 수정: `remote_service_script` 선두 `[ -t 0 ] && stty sane`, 로컬 `stty -g` 저장 후 EXIT trap 복구, sudo 비번 요구 시 사유 안내 메시지 추가
- 수정: 동일 버그(ControlMaster + `-t/-tt`) 인 `update-hexplorer-adapter-over-ssh.sh`, `change-jibot-network-over-ssh.sh`(2곳), `repair-adaptor-ipc-over-ssh.sh` 에도 prelude 적용

- 원인3(진짜 멈춤): `ControlMaster` + `-tt` + **`< /dev/tty` 리다이렉트** 3개가 겹치면 mux 마스터가 raw termios 를 복사할 뿐 아니라 **키 입력을 전부 삼킴** → sudo 프롬프트에 비번 입력 불가
  - 재현표: (mux+리다이렉트)=출력 `\n`+입력 무반응 / (mux, 리다이렉트 없음)=정상 / (mux 없음+리다이렉트)=정상
- 수정: `run_remote_tty()` 헬퍼 추가 — stdin 이 tty 면 리다이렉트 없이 상속, 아니면 기존 `< /dev/tty` 폴백. 호출 3곳 교체. hexplorer 도 동일 처리
- 확인: `--restart` 는 기본값 1(`RESTART_ADAPTER:-1`, 104줄). 끄려면 `--no-restart`

### 다음
- 사용자: 현재 터미널에서 `stty sane` 1회 실행(이전 Ctrl-C 로 raw 남아있을 수 있음) 후 재실행
- 비번 제거는 로봇에서 `/etc/sudoers.d/` 에 amr-xboxdrv NOPASSWD 추가 또는 `setup-adaptor-service.sh --no-venv` 재실행
- 참고(별건): `change-jibot-network-over-ssh.sh` 는 macOS 기본 bash 3.2 에서 파싱 불가(`$(cat <<'EOF' ...)` 구문) — 기존 문제

### 검증
- `bash -n` 양쪽 통과
- 실제 10.8.8.8 에 패치된 remote_service_script 를 ControlMaster+`-tt`+`</dev/tty` 로 실행 → 출력 `\r\n` 정상 복구 확인 (SERVICE_ACTION=bogus, 서비스 무영향)
- 배포된 `run_remote_tty()` 함수를 그대로 떼어내 pty 하네스(bash 스크립트 손자 프로세스)에서 실행 → `stdin-is-tty: yes`, 출력 `\r\n`, 틀린 비번에 `Sorry, try again` = **입력 전달 정상**
