# jibot-check

### 목표
- 10.8.8.8 jibot(7273) 다운 원인 규명

### 지금
- 22:19 사용자 재부팅(클린 warm reboot) → USB 16개 전부 복구, 22:24 7273 정상, 어댑터 연결됨
- 설계 논의: 워치독 reboot 에스컬레이션

### 완료
- 맥: en0 10.8.8.213/24 (로봇 내부 카메라 LAN 직결). ssh-copy-id 완료로 pubkey 접속 가능
- 로봇: hostname `ubuntu`, wlan0 192.168.101.61, eth0 10.8.8.8, 부팅 21:50:45
- urobot.service **정상 기동 중**(Type=simple, TimeoutStartSec=180, ExecStartPre sleep 30).
  이번 부팅에 5회 기동(21:51/21:55/21:58/21:59/22:03) — 전부 수동 restart, 크래시 아님
- jarvis_g 노드는 매번 뜸(25초 지연 후) → 그러나 51초 만에 자살
  `/usr/local/urobot/log/log-20260820-220400.log`:
    `mg_main: waiting for robot ready!` 반복 →
    `JSignal: exit[-1] without report: addr:":8500", reason:"Bot start failed!", run time 51s`
  → 그래서 7273 리스너가 절대 안 열림
- "robot ready"가 안 되는 이유 = 모션/센서 시리얼 링크 부재
  - `m04b_node`(left/right laser): `serialOpen(): cannot open serial port. No such file or directory` 반복
  - `odom` TF 프레임 부재 → NeoLocalization/tf2odom 경고 폭주, `DIDNT GET TRANSFORM map back_laser_link`
- **근본 원인: USB 장치가 하나도 열거되지 않음**
  - `/dev/ttyUSB*` 없음
  - `lsusb` = 루트 허브 6개뿐, 다운스트림 0개
  - `/sys/bus/usb/devices/` = usb1~6 + N-0:1.0 뿐
  - dmesg: ftdi_sio/pl2303/ch341 드라이버는 로드됐으나 `New USB device` 이벤트 0건
  - 직전 부팅(-1, 08-20 13:11~21:50)에는 정상: Genesys 허브(05e3:0610/0626),
    PL2303→ttyUSB4, r8152 USB NIC(eth01), 2dc8:6013 등 전부 인식됨
  - eth0(10.8.8.8)는 SoC 내장 `rk_gmac-dwmac`라 USB가 죽어도 살아있음 → ssh는 되는 것
- 인과 사슬: USB 미인식 → ttyUSB 없음 → 모션/센서 링크 없음 → odom 없음
  → jarvis-g `Bot start failed!` → 7273 미개방 → 어댑터 연결 불가
- **간헐성 확인 — 부팅별 USB 열거 수(`New USB device found`)**
  | boot | 시각 | FTDI(0403) | Genesys허브(05e3) | 총 USB장치 | jibot |
  |---|---|---|---|---|---|
  | -5 | 08-19 14:26 | 1 | 3 | 31 | 정상 |
  | -4 | 08-19 18:47 | 1 | 4 | 30 | 정상 |
  | -3 | 08-20 09:39 | 1 | 2 | 16 | 정상 |
  | -2 | 08-20 12:52 | 0 | 0 | **6(루트허브만)** | **실패** |
  | -1 | 08-20 13:11 | 1 | 3 | 27 | 정상(8.5시간) |
  |  0 | 08-20 21:50 | 0 | 0 | **6(루트허브만)** | **실패(현재)** |
  → 부팅 시 USB 허브 열거 실패가 간헐적으로 발생. 최근 6회 중 2회 실패.
    boot -2도 같은 증상이었고 **18분 뒤 재부팅(13:11)으로 정상 복구**됨
- boot -1은 클린 셧다운 마커 전무(`Reached target Shutdown` 등 없음), 21:50:12 정상 로그
  도중 뚝 끊김 → 전원 강제 차단. 33초 뒤 21:50:45 부팅되며 이번 실패 상태로 진입
- boot -1에서도 `serialOpen()` 실패는 있었으나 7273은 정상 동작 중이었음
  (좌/우 m04b 라이다 건은 이번 다운과 별개 이슈)

### 무재부팅 복구 가능성 (조사 결과: 거의 없음)
- `sudo` 비번 필요 → 원격에서 root 작업 불가
- `vcc5v0_host` = `regulator-always-on` (DT) → 소프트로 USB 5V 차단 불가
- USB 드라이버 전부 빌트인(`lsmod` 비어있음) → `modprobe -r xhci_hcd` 불가
- xhci 루트포트 `maxchild=1`, 아무것도 안 붙음. 27개 장치 전부 외장 허브(05e3) 하나에 물림
- 유일한 소프트 시도 = root로 dwc3 재바인딩
  `echo fe900000.usb | sudo tee /sys/bus/platform/drivers/dwc3/unbind` → `.../bind`
  단 실패 시 컨트롤러가 물릴 수 있고, 그러면 어차피 재부팅
- **이력상 복구된 유일한 사례(13:11)는 하드 전원 차단 후.** boot -1/-2/-3 종료 모두
  클린 셧다운 마커 없이 로그가 뚝 끊김 = 전원 강제 차단.
  warm reboot으로 복구된 전례는 관측 안 됨

### 기존 자동복구 장치 (이미 있음)
- `urobot-watchdog.service` + `.timer` (`/usr/local/sbin/urobot-watchdog`, Jun 25 설치)
- boot -2 때: `7273 still down after 3 restarts (state=active) — stopping auto-restart;
  suspect E-stop/MCU/hardware, manual check needed.`
- 이번 부팅에도 3회 재시작 후 포기 → 21:51/21:55/21:58/21:59 기동은 사용자가 아니라 워치독
- 현재도 ~65초마다 돌며 exit 1 기록 중
- 어댑터(run-adapter.sh)는 5초마다 재접속 시도 중 → 7273 뜨면 즉시 붙음

### 다음
- `systemctl restart urobot`은 무의미(USB가 없으니 몇 번을 해도 동일)
- 조치 순서:
  1) **비상정지(E-stop) 해제 여부 물리 확인** — 워치독도 E-stop 의심으로 남김.
     외장 허브가 주변전원(24V) 레일에서 급전되면 E-stop 시 정확히 이 증상(PC만 생존)
  2) USB 허브 전원/케이블 체결 확인
  3) **전원 완전 차단 후 재투입** — 검증된 유일한 복구 경로(13:11 사례)
  4) (선택) sudo 비번 제공 시 dwc3 unbind/bind 1회 시도. 확률 낮음
- 복구 판정 순서: `lsusb`에 다운스트림 장치 보임 → `ls /dev/ttyUSB*` → `ss -ltnp | grep 7273`
  (USB가 안 붙으면 jibot은 기다려도 안 뜸 — lsusb에서 바로 판정 가능)

### 검증
- ssh 원격 수집: systemctl status/journalctl -u urobot, ps aux, ss -ltnp, rosnode list,
  jarvis-g 자체 로그 `/usr/local/urobot/log/`, lsusb, /sys/bus/usb/devices, journalctl -k -b 0 vs -b -1

### 메모
- `/usr/local/urobot/logs/jarvis/urobot.launch`는 생성 직후 사라짐(08-10 세션의 미해결 의문 재현).
  단 이번 다운의 원인은 아님 — 별건으로 추적 필요
- docs/reference/jibot-onboard-access.md의 wlan0 IP(192.168.3.222/223)는 낡음. 실제 192.168.101.61


### 22:19 재부팅 결과 — 앞선 판단 정정
- **클린 warm reboot(`systemd-shutdown` 마커 5개)으로 USB 완전 복구.**
  FT4232H(0403:6011), PL2303, Genesys 허브 전부 재인식, `/dev/ttyUSB0~4` 생성
- 22:24 `7273 LISTEN (jarvis-g pid 6547)`, 어댑터 즉시 재연결
  (`battery=87 motor=True localization_score=1000 mode=Stop`)
- **정정**: "하드 전원 차단만 검증된 복구 경로"는 틀림. 상관표:

  | 종료 방식 | 다음 부팅 USB |
  |---|---|
  | 클린 reboot | 30 ✅ / 16 ✅ → **2/2 정상** |
  | 강제 차단 | 16 ✅ / 6 ❌ / 27 ✅ / 6 ❌ → **2/4 정상** |

  → `systemctl reboot`으로 충분. 오히려 강제 차단보다 성적이 좋음(표본 소수)
- 결론: **워치독에 reboot 에스컬레이션을 붙이는 것이 맞는 대응.**
  어댑터엔 `HostController.reboot()`(systemd.py:373)이 이미 있으므로 신규 개발이 아니라 연결 문제

### 설계 시 고려사항 (미착수)
- restart 3회 낭비 금지: `lsusb` 다운스트림 0개면 restart 무의미 → 즉시 reboot 단계로 직행
- reboot 루프 방지: 부팅당 1회. 재부팅 후에도 USB=0이면 물리 점검 알림으로 전환
- 알림 부재가 실제 공백 — 22:09 워치독이 포기했으나 아무도 몰랐음
- 어댑터는 로봇 온보드에서 실행 중(`run-adapter.sh`가 같은 호스트 journal에 기록)
- 이 실패 모드 오늘만 2회(12:52, 21:50) 발생 → 재발성 높음

### 설계 방향 (사용자 결정, 22:30경)
- **자동 재부팅 안 함.** 워치독은 지금처럼 restart 3회까지만
- 포기 시점에 **알림**을 띄워 사람이 인지
- **재부팅은 기능으로 만들되 FMS를 통해 사용자가 실행**
- → 경로 상향: bounded → **architectural** (MQTT 인터페이스 계약 변경 + 원격 파괴적 동작)

### 코드베이스 조사 결과
- 어댑터는 VDA5050. FMS 명령은 `instantActions`로 들어옴
- `adaptor/core/factsheet.py:13 INSTANT_ACTION_TYPES` 튜플에 액션 타입 등록
  (기존 40+개: cancelOrder, startCharging, jibotCommand, setMap, clamp, pioInit ...)
- 디스패치: `adaptor/adapter_jibot.py:5900~` `elif action.action_type == "..."` 체인
  + `self._action_registry.has(action.action_type)` 경로 (4436)
- 호스트 재부팅 구현 이미 존재: `adaptor/core/systemd.py:373 HostController.reboot()`
  (`sudo -n systemctl reboot`, polkit 룰은 scripts/setup-adaptor-service.sh 가 설치)
- WebUI 경로도 이미 존재: `adaptor/web/server.py:757 _post_host_reboot` (확인절차 + audit)
- 어댑터 IPC: `/run/amr-adaptor/<serial>/` 에 state/health 파일 (`core/ipc_paths.py`)
- 워치독 상태 파일: `/run/urobot-watchdog.count`, `/run/urobot-watchdog.gaveup`
  ※ `/run`이라 재부팅 시 초기화됨

### 미결 (다음 질문)
- 알림 채널: VDA5050 `errors` 배열 vs 별도 MQTT 토픽
- 재부팅 instantAction의 안전장치(확인 파라미터 등)
- 워치독(systemd/bash) → 어댑터(python) 상태 전달 방식

### 08-23 12:15 진행상황 점검
- **합의한 설계(①②) 미착수**: 스펙 문서 없음, `rebootHost` 없음,
  `adaptor/core/host_health.py` 없음, 진단 errorReferences(`usbDownstream` 등) 없음
- 다른 세션이 인접 작업 수행:
  - `30117fc` 워치독 재작성 `scripts/urobot-watchdog.sh`(신규). `/dev/ttyUSB*` 개수로
    USB 허브 미열거 vs 브링업 실패를 구분, 전자는 xHCI 리셋 후 urobot 재시작
  - `83db7e4` 설치 스크립트에 `--min-serial`(기본 4; 정상 5 = FT4232H 4 + PL2303 1)
  - `docs/superpowers/specs/2026-08-22-remote-config-reload-design.md`
    → `reloadConfig`/`restartAdapter` instantAction. **어댑터 재시작이지 호스트 재부팅 아님.**
      설계 확정·미구현. 우리 `rebootHost`는 이 패턴에 얹으면 됨
- **미배포**: 로봇의 `/usr/local/sbin/urobot-watchdog`는 6/25 구버전(2,230B), USB 판정 없음
  → 8/22 14:36, 19:35 두 번 다 구 로직대로 restart 3회 소진 후 포기
- 로봇 현재: 12:14:33 재부팅되어 `up 0 min`. 직전 스냅샷(uptime 1:44)에서는
  `/dev/ttyUSB0~4` 정상(=USB 고장 아님)인데 7273이 LISTEN·Recv-Q=6(미accept)이었음.
  재부팅 직전이라 wedge 확정은 못 함
