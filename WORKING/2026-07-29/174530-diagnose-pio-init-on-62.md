# diagnose-pio-init-on-62

### 목표
- 로봇 amr2(192.168.101.62)에서 `pioInit`이 제대로 동작하지 않는 원인을 찾는다.

### 지금
- **근본 원인 확정**: 62의 PIO 포트는 `/dev/ttyUSB0`인데 설정은 `/dev/ttyUSB4`다.
- 제안 1번(BC 응답 프레임 검증) 구현 완료.

### 완료 (수정 1번: BC 응답 프레임 검증)
- `pio_init`이 응답 유무만 보던 것을 `<...>` 프레임 포함 여부로 바꿨다.
  `find_pio_frame()` + `_PIO_FRAME_RE = <[^<>]+>` 추가.
- 실패 메시지에 **수신 원문 repr + 현재 pio_serial_port + `dmesg | grep pl2303` 안내**를
  담았다. 프레임 가정이 틀렸더라도 현장에서 원문을 보고 바로 판별·완화할 수 있게.
- 빈/공백 응답은 기존 "BC response timeout" 그대로 유지(원인이 다르다).
- `pioScenario`도 내부에서 `pio_init`을 부르므로 같이 보호된다.
- 테스트 2개 추가(`test_adapter_jibot_v3_order.py`):
  - `test_pio_init_rejects_line_noise_that_is_not_a_bc_frame` (RED 확인: True != False)
  - `test_pio_init_accepts_a_framed_reply_surrounded_by_noise` (과조임 방지 가드)
- `FakePioClient`에 `bc_response` 파라미터 추가, 기본값을 `"BC=OK"` →
  `"<BC=OK5C>"`로. 5C는 `checksum_hex("BC=OK")` 실제값.
- **미검증 가정**: 실장비의 BC 응답을 아직 한 번도 관측하지 못했다. TX가
  `<payload+checksum>`이라 RX도 같다고 본 것이며, 62에서 실제로 확인해야 한다.

### 근본 원인 (62 실측, 2026-07-29)
- `dmesg`: `pl2303 converter now attached to **ttyUSB0**` (bus 6-1),
  FTDI quad는 `ttyUSB1~4` (bus 1-1.1). **.61과 열거 순서가 반대다.**
  (.61: FTDI=ttyUSB0~3, PL2303=ttyUSB4)
- 62의 `config/extensions.hcl:8` = `pio_serial_port = "/dev/ttyUSB4"`
  → FTDI 포트(드라이브 MCU 계열)를 PIO로 알고 열고 있었다.
- pyserial 3.5는 비배타 open이라 **에러 없이 열리고** BC만 무응답.
  게다가 `pio_init`의 검증이 `response in (None,"")`뿐이라 잡음이 들어오면
  `ok:true`로 오보된다 → "되는 것 같은데 안 되는" 증상과 정확히 일치.
- 부수 위험: 그동안 `<BC=2:123456:250:0:OHT123..>` 프레임을 FTDI 장비에 쏘고 있었다.
- 반증된 후보: pyserial 미설치(3.5 설치됨), config.toml 잔존 [pio] 섹션(없음).
- 미해결 관측: `journalctl -u "amr-adaptor*"`가 빈 출력, WebUI 9000 closed
  → 62에서 어댑터/웹UI가 systemd 유닛으로 안 돌고 있을 가능성.
- 권한 주의: ttyUSB3/4만 `crwxrwxrwx`(누가 chmod 777), ttyUSB0~2는
  `crw-rw---- root:dialout`. ucore가 dialout 그룹이 아니면 ttyUSB0 열기 실패한다.

### 완료 (조사)
- 62 도달 확인: ping OK, 22 OPEN, 9001은 ROS `web_video_server`.
  **9000(우리 WebUI) closed** — amr-webui가 62에서 안 뜨고 있다.
- 유력 원인 후보(로컬에서 확인한 사실):
  1. **배포 시드 포트가 실측과 다르다.** 로봇에 심어지는 원본
     `adaptor/config/extensions.hcl:9`는 `pio_serial_port = "/dev/ttyUSB0"`인데,
     `docs/reference/jibot-ros-inventory.md:87` 실측(.61, 2026-07-29)은 PIO
     변환기가 PL2303 = `/dev/ttyUSB4`다. 빌드머신 `extensions.hcl:8`만 ttyUSB4로
     고쳐졌고 시드 파일은 ttyUSB0인 채 남았다.
     - ttyUSB0은 FTDI quad(`/dev/inner0`, 9600, 드라이브 MCU 추정) 쪽이다.
  2. **pyserial 3.5는 기본이 비배타 open**(`exclusive=None` → flock 안 검). 확인함.
     → 틀린 포트를 열어도 **에러 없이 열리고** BC만 무응답 → "BC response timeout",
     혹은 잡음이 들어오면 아래 3번 때문에 **ok:true로 오보**된다.
  3. `pio_init()`은 응답 검증이 `response in (None, "")`뿐이다
     (`adaptor/extensions/pio/__init__.py:162`). 9600 장비의 잡음 한 바이트라도
     들어오면 BC ack가 아닌데 성공으로 보고한다. "되는 것 같은데 안 된다"와 일치.
  4. 62 배포 시점에 따라 시드값이 더 옛날 `COM6`일 수도 있다(→ SerialException).
  5. pyserial 미설치 가능성: `requirements.txt`의 `pyserial==3.5`와
     `offline_packages/pyserial-3.5-py2.py3-none-any.whl`은 **아직 커밋 전**이고,
     `--install-py-deps`는 기본 꺼짐. 옛 스크립트는 glob이 `*-py3-none-any.whl`이라
     `py2.py3` 이름의 pyserial을 조용히 건너뛴다(이번 작업트리에서 고쳐짐).
     → 미설치면 `pioInit failed: No module named 'serial'`.

### 다음
- 62의 `config/extensions.hcl`을 `/dev/ttyUSB0`으로 고치고 재시작 → pioInit/pioPing 실행.
  실제 BC 응답 원문을 확보해 프레임 가정을 검증한다(틀리면 `_PIO_FRAME_RE` 완화).
- ucore의 dialout 그룹 여부 확인(ttyUSB0은 `crw-rw---- root:dialout`).
- 남은 제안: (2) 시드 extensions.hcl 포트 하드코딩 재검토 + by-path 권장,
  (3) jibot-ros-inventory.md에 62 실측 추가.
- `pio_ping`도 응답 유무만 본다 — 같은 오보 위험. 별도 판단 필요.

### 검증
- `pyserial 3.5 _reconfigure_port` 소스에서 `_exclusive is not None`일 때만 flock임을 확인.
- `grep pio_serial_port`로 시드(ttyUSB0)와 빌드머신(ttyUSB4) 불일치 확인.
- RED 확인: 새 테스트가 `AssertionError: True != False`로 실패(잡음이 성공 처리됨).
- GREEN: `adaptor/tests` 1404 passed, 저장소 `tests/` 117 passed.
