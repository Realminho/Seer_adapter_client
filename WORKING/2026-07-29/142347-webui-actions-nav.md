# webui-actions-nav

### 앞선 작업 (완료, 미커밋)
- nav에 Actions 추가 + 전역 `/actions` 라우트(1대면 302, 여럿이면 picker).
  `web/render.py:_nav`, `actions_index_page()`, `web/server.py` GET `/actions`.
- `extensions/pio/__init__.py:action_specs()`에 `pioInit`(media/stationId/channel/
  port/timeoutSec, 앞 넷 required)과 `pioReadIn`(channel/timeoutSec) 파라미터 선언.
  `ohtNumber`는 로봇 고유값이라 칸을 두지 않음.
- 검증: adaptor `tests/` 1345 passed, 리포 루트 `tests/` 109 passed.

### 목표
- "PIO 연결이 안 된다"의 근본 원인 특정.

### 지금
- Phase 1 완료. 로컬에서 재현했고, 로봇 확인은 접근 수단이 없어 막혀 있다.

### 완료 (조사)
- **로컬 재현**: 현재 config로 `execute_pio_action(pioInit)` 실행 ->
  `ok=False, message="pioInit failed: No module named 'serial'",
  reason=ModuleNotFoundError`.
- **pyserial이 리포 어디에도 선언돼 있지 않다.** `adaptor/requirements.txt`는
  paho-mqtt / tomli / python-hcl2 셋뿐이고, `pyserial` 문자열이 리포 전체에 0건.
  배포 스크립트의 `--install-py-deps`도 이걸 못 깐다.
- `utils/pio.py:1`이 `import serial`이지만 호출부 3곳(`extensions/pio:59`,
  `utils/elevator.py:290`, `utils/airshower.py:254`)이 전부 함수 안에서 지연
  import이라 어댑터는 정상 부팅하고 PIO 액션만 실패한다 — 증상과 일치.
- 로컬 2차 장애물: `extensions.hcl pio_port = "COM6"`(Linux 부적합),
  이 머신엔 `/dev/ttyUSB*`/`ttyACM*`가 아예 없음. 로컬엔 어댑터 프로세스도 없음.
- 로봇 192.168.101.61: 22/9000 열려 있으나 인증 불가.
  ssh 키가 passphrase를 요구하고 agent 미기동(`ssh-add -l` -> agent 없음),
  WebUI는 Basic 인증 자격증명이 별도 TOML이라 로컬에 없음.

### 로봇 실측 (2026-07-29, ucore@192.168.101.61)
- `.venv/bin/python -c "import serial"` -> **ModuleNotFoundError** (pyserial 없음)
- `/dev/ttyUSB0` ~ `/dev/ttyUSB4` 존재 (하드웨어는 정상), ttyACM 없음
- `config/extensions.hcl`의 `pio_port = "COM6"` — 시드된 기본값 그대로
- 즉 1차(import) / 2차(포트명) 두 장애물이 동시에 걸려 있다.

### 완료 (수정)
- `adaptor/requirements.txt`에 `pyserial==3.5` 추가 (기존 3줄에 없었다).
- `adaptor/config/extensions.hcl`의 `pio_port`를 `/dev/ttyUSB0`으로. 이 파일이
  배포 시 로봇에 시드되는 원본이라 COM6가 계속 퍼지고 있었다.
- 회귀 테스트 2개(`tests/test_config.py`):
  `test_pyserial_is_declared_because_pio_needs_it`,
  `test_shipped_pio_port_is_a_posix_device`.

### PIO 포트 확정 (2026-07-29)
- 케이블 재삽입 dmesg: `pl2303 converter now attached to ttyUSB4`
  (idVendor=067b idProduct=2303, SerialNumber=0). 사용자가 마지막에 꽂은 게 PIO.
- `sudo lsof /dev/ttyUSB4` -> 비어 있음. 즉 PIO가 써도 충돌 없음.
- **문서 정정 필요**: `docs/reference/jibot-ros-inventory.md:87`은 ttyUSB4(PL2303)를
  트랙 센서로 기록하나 이 호기에선 아무도 안 잡고 있다.
- PL2303이 SerialNumber=0이라 by-id 이름이 장치 고유값이 아니다 -> by-path 권장.
- 로컬 venv에 `uv pip install pyserial==3.5` 완료. 재현 스크립트가
  ModuleNotFoundError -> SerialException(포트 없음)으로 이동함을 재확인.
- 로봇 전달용 휠 확보: scratchpad/pyserial-3.5-py2.py3-none-any.whl (pure python이라
  로봇이 ARM이어도 그대로 쓸 수 있다 — `--copy-venv`는 arch가 달라 불가).

### 완료 (개명: pio_port -> pio_serial_port)
- 사용자 결정: 이름은 `pio_serial_port`(USB로 못 박지 않음 — 이 로봇엔 /dev/ttyS1도
  있어 나중에 네이티브 UART로 갈 수 있다), 호환은 **새 키만 인식**.
- 11개 파일 16곳 치환. `adapter_jibot.py`의 io 스냅샷 JSON 키 `"port"`는 표시용이라
  그대로 두고 읽는 attribute만 바꿨다.
- 옛 키가 남으면 `PioConfig(**...)`가 TypeError로 죽어 원인을 알 수 없다.
  병합 **전에** config.toml / extensions.hcl 각각을 보고 **어느 파일**에 옛 키가
  있는지 짚어 ExtensionsError로 올린다.
- `[pio]`는 2b0ce69(7/28)에서 config.toml -> extensions.hcl로 옮겨졌지만 배포
  기본값이 `CONFIG_TOML_MODE=keep`이라 그 이전 로봇은 자기 config.toml에 옛 [pio]를
  갖고 있다. 그래서 두 파일 다 검사한다.
- 테스트 3개 추가(정상 로드 / extensions.hcl 옛 키 / config.toml 옛 키).

### 완료 (PIO 설정 단일 출처화)
- `config.toml`에 `pio` / `pio_advanced` / `air_shower_pio` / `elevator_pio`가
  있으면 병합 전에 ExtensionsError로 부팅을 멈추고 "지우세요"라고 말한다.
  `config/config.py:_PIO_SECTIONS`.
- `ezi`는 PIO가 아니라 제외했다 — 같은 규칙을 걸면 현장 로봇이 예고 없이 부팅에
  실패하므로 별도 결정이 필요하다. 그 취지를 테스트로 고정
  (`test_config_toml_may_still_define_non_pio_sections`).
- 개명 검사는 이제 extensions.hcl만 본다(config.toml 경로는 위 섹션 검사가 먼저
  잡는다). 그에 따라 `test_legacy_pio_port_in_config_toml_names_config_toml`은
  의도가 낡아 삭제 — 새 섹션 테스트가 더 넓게 덮는다.

### 완료 (오프라인 휠 glob)
- `*-py3-none-any.whl` -> `*-none-any.whl`. 세 스크립트 모두 같은 결함이었다:
  `update-jibot-adapter-over-ssh.sh:698`, `setup-adaptor-service.sh:191`,
  `install-offline-python-deps.sh:62`.
- `*.whl`로 넓히지 않은 이유: native 휠(manylinux)을 purelib에 풀어버린다.
  `*-none-any`가 pure Python 휠만 정확히 고른다. 테스트가 양쪽을 다 고정한다.
- 기존 `test_python38_webui_tomli_dependency_is_installed`가 옛 glob 문자열을
  리터럴로 박아둬서 같이 갱신했다.

### 다음
- 로봇 venv에 pyserial 설치. venv는 uv로 만들어 pip이 없다(로컬도 동일) —
  `--copy-venv`는 arch가 달라 불가. 휠을 직접 풀어넣거나 offline_packages에
  넣고 `--install-py-deps`를 쓴다(이제 glob이 py2.py3도 잡는다).
- ttyUSB0~4 중 어느 것이 PIO인지 현장 확인(`udevadm info /dev/ttyUSB*`).
  로봇의 extensions.hcl은 자기 파일이 보존되므로 거기서 직접 고쳐야 한다.

### 검증
- 재현 스크립트: scratchpad/repro_pio_init.py (현재 config 그대로 태움).
- 수정 전: `pioInit failed: No module named 'serial'` (ModuleNotFoundError)
- pyserial 주입 후: `could not open port /dev/ttyUSB0: No such file or directory`
  (SerialException) — 이 머신엔 시리얼 장치가 없으니 정상. 실패 지점이 import에서
  포트 열기로 이동한 것을 실측했다.
- adaptor `tests/` 1355 passed, 리포 루트 `tests/` 109 passed.
