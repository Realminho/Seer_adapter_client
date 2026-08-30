# xboxdrv-protocol-check

### 목표
- joystick bridge를 정식 설치·업데이트·서비스 제어 경로에 편입한다.

### 지금
- 스크립트, unit, 배포 경로, 문서, 테스트 반영 완료. AMR 실기 검증만 남았다.

### 완료
- 근본 원인: receiver가 controller 상태에 따라 재열거된다(잠듦 3109, 연결 310b).
  xboxdrv는 시작 시 USB handle을 한 번만 잡고, 장치가 사라져도 종료하지 않아
  `Restart=always`가 발동하지 않는다. 재부팅뿐 아니라 controller 수면 사이클마다 재현된다.
- `scripts/amr-xboxdrv-run.sh`가 310b 존재를 감시한다. 자식 종료는 SIGTERM을 쓴다.
  비대화형 셸의 백그라운드 자식은 SIGINT를 무시하도록 상속받기 때문이다.
- unit의 `ExecStart`는 `__SCRIPTSDIR__`로 배포된 scripts/를 가리킨다. over-ssh update가
  supervisor까지 갱신한다.
- setup에 `install_amr_xboxdrv_service` 추가. xboxdrv 바이너리가 없으면 경고 후 skip,
  `--no-xboxdrv`로 제외, `usermod -aG input`, enable, 재시작(adapter보다 먼저) 포함.
- adaptor-services.sh가 bridge를 adapter보다 먼저 제어한다. uninstall과 sudoers에도 포함.
- over-ssh update가 bridge를 함께 stop/start 한다. 업로드가 실행 중인 셸 스크립트를
  덮어쓰면 셸이 offset 기준으로 잘못 읽기 때문이다.
- polkit에는 넣지 않았다. WebUI에 해당 controller가 없어 dead grant가 된다.

### 다음
- AMR에서 `scripts/setup-adaptor-service.sh --jibot` 실행 후 controller 수면 사이클과
  재부팅을 검증한다. 검증되면 커밋한다(전부 아직 untracked/미커밋).

### 검증
- `./scripts/run-tests.sh -o addopts="" ../tests` 118 passed.
- 사전 실패 2건은 HEAD(9a0e4c0) worktree에서도 동일하게 실패한다. 이번 변경과 무관하다:
  `test_run_adapter_dispatches_multi_robot_fleet_to_run_multi`,
  `test_update_script_restarts_after_upload_with_tty`.
- setup/adaptor-services/uninstall `--dry-run` 출력으로 렌더링·순서·sudoers를 확인했다.
